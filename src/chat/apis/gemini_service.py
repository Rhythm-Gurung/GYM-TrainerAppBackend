from google import genai
from google.genai import types
from google.api_core.exceptions import ResourceExhausted
from django.conf import settings


CLIENT_SYSTEM_INSTRUCTION = """You are a helpful fitness coach and personal training assistant. When responding:
- Answer fitness and health-related questions
- Provide workout recommendations based on user questions
- Offer motivational support
- Answer frequently asked questions about fitness, training, and wellness
- Give personalized suggestions when relevant
- Be friendly and encouraging"""

TRAINER_SYSTEM_INSTRUCTION = """You are an expert fitness trainer assistant. When responding:
- Provide practical workout advice and tips
- Ask clarifying questions about fitness goals
- Suggest evidence-based fitness recommendations
- Be encouraging and motivational
- Address any fitness or health-related questions"""

# Module-level singleton — initialized once, reused across all requests
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


class GeminiService:
    """Service to handle Gemini API interactions"""

    MODEL = "gemini-2.5-flash-lite"
    _ALLOWED_ROLES = {"user", "model"}

    def __init__(self):
        self.client = _get_client()

    def _build_contents(self, user_message: str, conversation_history: list) -> list:
        """
        Build a structured contents list from conversation history.
        - Maps 'assistant' -> 'model' (SDK requirement)
        - Strict allowlist: skips any role not in {'user', 'model'}
        - Always ends with the current user message
        """
        contents = []

        for msg in conversation_history[-10:]:
            raw_role = msg.get("role", "user")
            role = "model" if raw_role == "assistant" else raw_role

            if role not in self._ALLOWED_ROLES:
                continue

            content = msg.get("content", "")
            contents.append(
                types.Content(role=role, parts=[types.Part(text=content)])
            )

        contents.append(
            types.Content(role="user", parts=[types.Part(text=user_message)])
        )
        return contents

    def _generate(self, user_message: str, system_instruction: str, conversation_history: list = None) -> str:
        """Shared core generation logic used by all endpoints."""
        contents = self._build_contents(user_message, conversation_history or [])
        config = types.GenerateContentConfig(system_instruction=system_instruction)

        try:
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=contents,
                config=config,
            )
        except ResourceExhausted:
            return "The gym is a bit crowded right now. Please wait a few seconds and try again!"
        except Exception:
            return "The fitness coach is currently busy. Please try again in a moment!"

        candidate = response.candidates[0] if response.candidates else None
        if not candidate:
            raise Exception("No response candidates returned.")
        if candidate.finish_reason.name == "SAFETY":
            return "I'm sorry, I can't answer that based on safety guidelines."

        return response.text

    def generate_client_response(self, user_message: str, conversation_history: list = None) -> str:
        return self._generate(user_message, CLIENT_SYSTEM_INSTRUCTION, conversation_history)

    def generate_trainer_response(self, user_message: str, conversation_history: list = None) -> str:
        return self._generate(user_message, TRAINER_SYSTEM_INSTRUCTION, conversation_history)
