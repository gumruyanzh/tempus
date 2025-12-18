"""DeepSeek API integration service for tweet generation."""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.models.tweet import TweetTone

logger = get_logger(__name__)


class DeepSeekAPIError(Exception):
    """DeepSeek API error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class DeepSeekService:
    """Service for DeepSeek LLM API operations."""

    DEFAULT_MODEL = "deepseek-chat"

    TONE_PROMPTS = {
        TweetTone.PROFESSIONAL: """Write in a professional, business-appropriate tone.
Use industry terminology where appropriate. Be concise and authoritative.""",
        TweetTone.CASUAL: """Write in a casual, friendly tone.
Use conversational language and be relatable. Feel free to use common expressions.""",
        TweetTone.VIRAL: """Write content optimized for engagement and virality.
Use attention-grabbing language, ask questions, or make bold statements.
Include elements that encourage sharing and responses.""",
        TweetTone.THOUGHT_LEADERSHIP: """Write as a thought leader sharing valuable insights.
Provide unique perspectives, share wisdom, and position the content as authoritative
yet accessible. Include forward-thinking ideas.""",
    }

    DEFAULT_SYSTEM_PROMPT = """You are a human thought leader who shares genuine insights on Twitter/X.
Your goal is to write tweets that feel like they came from a real person, not a content machine.

CRITICAL RULES:
1. Each tweet MUST be 280 characters or less
2. Do not use hashtags unless explicitly requested
3. Write like you're texting a smart friend, not writing marketing copy
4. NEVER start tweets with "The [topic] is..." or similar generic patterns
5. Vary your openings: questions, personal observations, contrarian takes, numbers, stories
6. Avoid clichés like: "game-changer", "the future of", "here's why", "unpopular opinion"
7. Use natural language with occasional incomplete sentences, dashes, or parenthetical thoughts
8. Each tweet should have a unique hook - never repeat the same structure

{tone_instructions}

Output format:
- For single tweets: Return ONLY the tweet text, nothing else
- For threads: Return each tweet on a separate line, numbered (1., 2., 3., etc.)
"""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.deepseek_api_base_url,
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def generate_tweet(
        self,
        prompt: str,
        tone: TweetTone = TweetTone.PROFESSIONAL,
        custom_system_prompt: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> str:
        """Generate a single tweet."""
        system_prompt = self._build_system_prompt(tone, custom_system_prompt)

        instructions_text = f"\n\nAdditional instructions: {instructions}" if instructions else ""

        user_prompt = f"""Generate a single tweet based on the following:

Topic/Content: {prompt}{instructions_text}

Remember: Maximum 280 characters. Return ONLY the tweet text."""

        response = await self._call_api(system_prompt, user_prompt)
        tweet = self._clean_tweet_response(response)

        # Validate length
        if len(tweet) > 280:
            # Try to regenerate with stricter instruction
            user_prompt = f"""Generate a single tweet based on the following:

Topic/Content: {prompt}{instructions_text}

IMPORTANT: Your response was too long. The tweet MUST be under 280 characters.
Return ONLY the tweet text, no explanations."""

            response = await self._call_api(system_prompt, user_prompt)
            tweet = self._clean_tweet_response(response)

            # If still too long, truncate
            if len(tweet) > 280:
                tweet = tweet[:277] + "..."

        logger.info(
            "Tweet generated",
            tone=tone.value,
            character_count=len(tweet),
        )

        return tweet

    async def generate_thread(
        self,
        prompt: str,
        num_tweets: int = 3,
        tone: TweetTone = TweetTone.PROFESSIONAL,
        custom_system_prompt: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> list[str]:
        """Generate a thread of tweets."""
        if num_tweets < 2:
            num_tweets = 2
        if num_tweets > 10:
            num_tweets = 10

        system_prompt = self._build_system_prompt(tone, custom_system_prompt)

        instructions_text = f"\n\nAdditional instructions: {instructions}" if instructions else ""

        user_prompt = f"""Generate a Twitter thread with exactly {num_tweets} tweets based on the following:

Topic/Content: {prompt}{instructions_text}

Requirements:
1. Each tweet must be under 280 characters
2. Number each tweet (1., 2., 3., etc.)
3. First tweet should hook the reader
4. Each tweet should be able to stand alone but contribute to the overall narrative
5. Last tweet should provide a conclusion or call to action

Return the tweets numbered, one per line."""

        response = await self._call_api(system_prompt, user_prompt)
        tweets = self._parse_thread_response(response)

        # Validate and truncate if needed
        validated_tweets = []
        for tweet in tweets:
            if len(tweet) > 280:
                tweet = tweet[:277] + "..."
            validated_tweets.append(tweet)

        logger.info(
            "Thread generated",
            tone=tone.value,
            tweet_count=len(validated_tweets),
        )

        return validated_tweets

    async def improve_tweet(
        self,
        original_tweet: str,
        tone: TweetTone = TweetTone.PROFESSIONAL,
        feedback: Optional[str] = None,
    ) -> str:
        """Improve an existing tweet."""
        system_prompt = self._build_system_prompt(tone)

        feedback_text = f"\nUser feedback: {feedback}" if feedback else ""

        user_prompt = f"""Improve the following tweet while maintaining its core message:

Original tweet: {original_tweet}
{feedback_text}

Requirements:
1. Keep it under 280 characters
2. Make it more engaging
3. Maintain the original intent
4. Apply the specified tone

Return ONLY the improved tweet text."""

        response = await self._call_api(system_prompt, user_prompt)
        tweet = self._clean_tweet_response(response)

        if len(tweet) > 280:
            tweet = tweet[:277] + "..."

        return tweet

    async def validate_api_key(self) -> bool:
        """Validate that the API key is working."""
        try:
            client = await self.get_client()
            response = await client.post(
                "/chat/completions",
                json={
                    "model": self.DEFAULT_MODEL,
                    "messages": [
                        {"role": "user", "content": "Say 'OK'"}
                    ],
                    "max_tokens": 10,
                },
            )
            return response.status_code == 200
        except Exception as e:
            logger.error("API key validation failed", error=str(e))
            return False

    def _build_system_prompt(
        self,
        tone: TweetTone,
        custom_prompt: Optional[str] = None,
    ) -> str:
        """Build the system prompt with tone instructions."""
        tone_instructions = self.TONE_PROMPTS.get(
            tone,
            self.TONE_PROMPTS[TweetTone.PROFESSIONAL],
        )

        base_prompt = custom_prompt or self.DEFAULT_SYSTEM_PROMPT
        return base_prompt.format(tone_instructions=tone_instructions)

    async def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Make API call to DeepSeek."""
        client = await self.get_client()

        payload = {
            "model": self.DEFAULT_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1000,
            "temperature": 0.7,
        }

        try:
            response = await client.post("/chat/completions", json=payload)

            if response.status_code != 200:
                error_text = response.text
                logger.error(
                    "DeepSeek API error",
                    status_code=response.status_code,
                    response=error_text,
                )
                raise DeepSeekAPIError(
                    f"API request failed: {error_text}",
                    status_code=response.status_code,
                )

            data = response.json()
            return data["choices"][0]["message"]["content"]

        except httpx.RequestError as e:
            logger.error("DeepSeek API request error", error=str(e))
            raise DeepSeekAPIError(f"Request failed: {str(e)}")

    @staticmethod
    def _clean_tweet_response(response: str) -> str:
        """Clean up the API response to extract the tweet."""
        tweet = response.strip()

        # Remove common wrapper patterns
        if tweet.startswith('"') and tweet.endswith('"'):
            tweet = tweet[1:-1]
        if tweet.startswith("'") and tweet.endswith("'"):
            tweet = tweet[1:-1]

        # Remove any numbering if present
        import re

        tweet = re.sub(r"^\d+\.\s*", "", tweet)

        return tweet.strip()

    @staticmethod
    def _parse_thread_response(response: str) -> list[str]:
        """Parse a thread response into individual tweets."""
        import re

        lines = response.strip().split("\n")
        tweets = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Remove numbering (1., 2., etc.)
            line = re.sub(r"^\d+\.\s*", "", line)

            # Remove quotes
            if line.startswith('"') and line.endswith('"'):
                line = line[1:-1]
            if line.startswith("'") and line.endswith("'"):
                line = line[1:-1]

            if line:
                tweets.append(line.strip())

        return tweets
