"""Durable replay snapshots. Never execute SQL or infer completion from an absent lock."""
import asyncio
import hashlib
import json
import logging

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlmodel import Session

from apps.chat.models.chat_model import ChatRecord, ChatRecordResult, ChatLog
from common.core.db import engine
from common.core.question_gate import GateRejected

logger = logging.getLogger(__name__)
REPLAY_TTL = 600
TERMINAL = ('SUCCESS', 'FAILED')


def fingerprint(question, options):
    # Capture original command before regenerate rewrites the question text.
    value = {'question': question.question, 'options': options}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


def lookup(chat_id, request_id, identity_only=False):
    with Session(engine) as session:
        fields = ((ChatRecord.id, ChatRecord.status, ChatRecord.request_fingerprint)
                  if identity_only else (ChatRecord,))
        result = session.execute(select(*fields).where(
            ChatRecord.chat_id == chat_id, ChatRecord.request_id == request_id))
        return result.one_or_none() if identity_only else result.scalar_one_or_none()


def snapshot(record):
    from apps.chat.curd.chat import format_record
    with Session(engine) as session:
        usages = session.execute(select(ChatLog.token_usage).where(ChatLog.pid == record.id)).scalars().all()
    tokens = 0
    for usage in usages:
        if isinstance(usage, str):
            try:
                usage = json.loads(usage)
            except ValueError:
                continue
        if isinstance(usage, dict):
            tokens += int(usage.get('total_tokens') or 0)
        elif isinstance(usage, (int, float)):
            tokens += int(usage)
    duration = ((record.finish_time - record.create_time).total_seconds()
                if record.finish_time and record.create_time else None)
    result = ChatRecordResult(**record.model_dump(), total_tokens=tokens, duration=duration)
    return jsonable_encoder({
        'replay': True, 'request_id': record.request_id, 'chat_id': record.chat_id,
        'record_id': record.id, 'status': record.status,
        'request_fingerprint': record.request_fingerprint,
        'record': format_record(result),
    })


def finalize(record_id, failed, lease):
    with Session(engine) as session:
        record = session.get(ChatRecord, record_id)
        if record is None or record.status != 'PROCESSING':
            return record
        lease.ensure_active()
        if failed or record.error or not record.finish:
            record.status = 'FAILED'
            record.error = record.error or '本次执行未获得完整结果，请重新提问。'
        else:
            record.status = 'SUCCESS'
        from datetime import datetime
        record.finish = True
        record.finish_time = record.finish_time or datetime.now()
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


async def cache_result(gate, payload):
    try:
        await gate.redis.set(gate.keys.request_replay(payload['chat_id'], payload['request_id']),
                             json.dumps(payload, ensure_ascii=False), ex=REPLAY_TTL)
    except Exception:
        # Durable completion is authoritative even when cache refill fails.
        logger.warning('Replay cache write failed; database fallback remains available', exc_info=True)


async def cached_result(gate, chat_id, request_id, expected_fingerprint):
    if gate.redis is None:
        raise GateRejected('COORDINATION_UNAVAILABLE', 'Request coordination is unavailable', 503)
    try:
        raw = await gate.redis.get(gate.keys.request_replay(chat_id, request_id))
    except Exception:
        raise GateRejected('COORDINATION_UNAVAILABLE', 'Request coordination is unavailable', 503) from None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        valid = (payload['chat_id'] == chat_id and payload['request_id'] == request_id
                 and payload['status'] in TERMINAL and isinstance(payload['record'], dict))
    except (ValueError, KeyError, TypeError):
        return None
    if not valid:
        return None
    # A cached snapshot must not resurrect a deleted record.
    record = await asyncio.to_thread(lookup, chat_id, request_id, True)
    if record is None or record.id != payload['record_id'] or record.status not in TERMINAL:
        return None
    check_identity(record, expected_fingerprint)
    return payload


def check_identity(record, expected_fingerprint):
    if record.request_fingerprint != expected_fingerprint:
        raise GateRejected('REQUEST_ID_CONFLICT', '同一 request_id 不能用于不同的提问或操作。')


async def database_result(gate, chat_id, request_id, expected_fingerprint):
    record = await asyncio.to_thread(lookup, chat_id, request_id)
    if record is None:
        return None
    check_identity(record, expected_fingerprint)
    if record.status not in TERMINAL:
        raise GateRejected('RESULT_UNAVAILABLE', '本次执行未获得完整结果，请重新提问。')
    payload = await asyncio.to_thread(snapshot, record)
    await cache_result(gate, payload)
    return payload


async def complete_result(gate, lease, failed):
    if lease.record_id is None:
        return
    record = await asyncio.to_thread(finalize, lease.record_id, failed, lease)
    if record and record.status in TERMINAL:
        payload = await asyncio.to_thread(snapshot, record)
        await cache_result(gate, payload)
