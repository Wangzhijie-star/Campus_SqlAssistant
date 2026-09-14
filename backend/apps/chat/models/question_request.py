from typing import Annotated

from fastapi import Body
from pydantic import BaseModel, StringConstraints


RequestId = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]


class ChatQuestionBase(BaseModel):
    question: str = Body(description='用户提问')
    chat_id: int = Body(description='会话ID')
    request_id: RequestId = Body(description='本次操作ID，由调用方生成；网络重试复用，重新生成答案使用新ID')
