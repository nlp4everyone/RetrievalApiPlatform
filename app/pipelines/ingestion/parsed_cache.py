"""Where a parsed document is stored, and how it is addressed.

ParseStage reads through this module and PersistTextStage writes through it,
so the two can never disagree about where an artifact lives.

The key is content-addressed but deliberately *not* global:

- ``api_key`` scopes every artifact to the account that uploaded the file.
  Sharing one cache across accounts would leak by timing (a hit returns in
  milliseconds, a miss takes as long as the parsing API) and would make
  erasing an account's data impossible without reference counting.
- ``provider`` is part of the key because a parse result is a function of the
  parser as much as of the file. Without it, switching PDF_PARSER_PROVIDER
  would silently keep serving the previous backend's output.
- ``CACHE_VERSION`` is the manual escape hatch for everything the provider
  name does not capture - parser options, prompt changes, a provider-side
  model upgrade. Bump it and every artifact is re-parsed on next ingestion.
"""

# Bump when the parsing options change in a way that should invalidate
# everything already stored.
CACHE_VERSION = "v1"


def parsed_text_key(api_key: str, provider: str, content_sha256: str) -> str:
    """
    Object key for one file's parsed Markdown.

    Args:
        api_key: Account that owns the file, scoping the artifact to it
        provider: Parsing backend that produced the text, e.g. "llamaparse"
        content_sha256: Hex digest of the file's bytes

    Returns:
        str: Object path within PARSED_TEXT_BUCKET
    """
    return f"parsed/{api_key}/{provider}/{CACHE_VERSION}/{content_sha256}.md"