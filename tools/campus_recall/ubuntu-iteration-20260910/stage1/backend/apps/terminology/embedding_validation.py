"""Terminology length validation; no embedding forward pass is performed here."""
from apps.ai_model.embedding import EmbeddingModelCache


def build_terminology_text(word, description):
    # Same separator as the evaluated description strategy.
    return word.strip() + '。' + description.strip()


def tokenizer_and_limit():
    wrapper = EmbeddingModelCache.get_model()
    # Isolate the LangChain adapter dependency in one place and fail explicitly.
    client = getattr(wrapper, '_client', None)
    if client is None or not hasattr(client, 'tokenizer'):
        raise RuntimeError('无法取得当前向量模型的 tokenizer，请检查模型适配器')
    limits = [getattr(client, 'max_seq_length', None),
              getattr(client.tokenizer, 'model_max_length', None)]
    limits = [int(n) for n in limits if isinstance(n, (int, float)) and 0 < n < 1000000]
    if not limits:
        raise RuntimeError('无法确定当前向量模型的有效 token 上限')
    return client.tokenizer, min(limits)


def validate_terminology_group(word, description, other_words=None):
    if not word or not word.strip():
        raise ValueError('术语名称不能为空')
    if not description or not description.strip():
        raise ValueError('术语解释不能为空')
    names = [word.strip()] + [w.strip() for w in (other_words or []) if w and w.strip()]
    tokenizer, limit = tokenizer_and_limit()
    texts = [build_terminology_text(w, description) for w in names]
    encoded = tokenizer(texts, add_special_tokens=True, truncation=False, padding=False)
    counts = [len(ids) for ids in encoded['input_ids']]
    errors = []
    for index, (name, count) in enumerate(zip(names, counts)):
        if count > limit:
            kind = '主术语' if index == 0 else '同义词'
            errors.append(f'{kind}“{name}”与解释拼接后为 {count} tokens，超过模型上限 {limit}'
                          '（包含特殊 token），请缩减名称或解释；本组未保存')
    if errors:
        raise ValueError('；'.join(errors))
    return {'limit': limit, 'counts': counts, 'names': names}
