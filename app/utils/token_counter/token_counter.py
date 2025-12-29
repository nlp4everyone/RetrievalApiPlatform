from typing import Union, List
import tiktoken
model_name = "gpt-3.5-turbo"
encoding = tiktoken.encoding_for_model(model_name)


def approximate_count_tokens(messages: Union[str,List[dict]]) -> int:
    if isinstance(messages, dict):
        messages = [messages]
    # Tokenize and count tokens
    if isinstance(messages, str):
        return len(encoding.encode(messages))

    total_tokens = 0

    for message in messages:
        total_tokens += 4  # Approx token overhead per message
        total_tokens += len(encoding.encode(message.get("content")))

    total_tokens += 2  # Assistant reply overhead
    return total_tokens


