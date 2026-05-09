import logging
from typing import Optional

logger = logging.getLogger(__name__)


def apply_variables(text: str, user_name: str, char_name: Optional[str] = None) -> str:
    """
    Substitute {{user}} and {{char}} template variables in text.

    char_name=None is valid for Director-context calls (Stage 2+) where no
    single active speaker exists. In that case {{char}} is left literally in
    place and a DEBUG warning is logged. Author content should not reference
    {{char}} in Director-facing fields (scenario summaries, beat descriptions).
    """
    result = text.replace("{{user}}", user_name)
    if char_name is not None:
        result = result.replace("{{char}}", char_name)
    elif "{{char}}" in result:
        logger.debug(
            "apply_variables: {{char}} found but char_name=None — "
            "leaving literal in place. "
            "Check author content for {{char}} in Director-facing fields."
        )
    return result
