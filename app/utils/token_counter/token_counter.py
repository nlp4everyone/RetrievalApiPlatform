from typing import Union, List
import tiktoken

# Default model for tokenization - using gpt-3.5-turbo as it's widely supported
MODEL_NAME = "gpt-3.5-turbo"
# Initialize encoding for the specified model
encoding = tiktoken.encoding_for_model(MODEL_NAME)


def approximate_count_tokens(messages: Union[str, List[dict]]) -> int:
    """
    Approximate the number of tokens in text or message format.
    
    This function provides an approximate token count for both plain text strings
    and structured message arrays (like OpenAI chat messages). The approximation
    includes overhead for message structure and formatting.
    
    Args:
        messages: Either a plain text string or a list of message dictionaries.
                 For message dictionaries, expects 'content' key in each message.
                 Can also be a single message dict which will be converted to list.
        
    Returns:
        int: The approximate token count
        
    Raises:
        KeyError: If message dictionaries don't contain 'content' key
    """
    # Convert single message dict to list for uniform processing
    if isinstance(messages, dict):
        messages = [messages]
    
    # Handle plain text string input
    if isinstance(messages, str):
        return len(encoding.encode(messages))

    total_tokens = 0

    # Process each message in the list
    for message in messages:
        # Add approximate overhead per message (role, formatting, etc.)
        total_tokens += 4  # Approx token overhead per message
        total_tokens += len(encoding.encode(message.get("content")))

    # Add overhead for assistant reply in conversation format
    total_tokens += 2  # Assistant reply overhead
    
    return total_tokens


