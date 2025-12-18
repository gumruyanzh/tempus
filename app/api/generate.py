"""Tweet generation routes using DeepSeek API."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, RateLimitedUser
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import generate_csrf_token
from app.models.tweet import TweetTone
from app.models.user import APIKeyType
from app.services.audit import AuditService
from app.services.deepseek import DeepSeekAPIError, DeepSeekService
from app.services.tweet import TweetService
from app.services.user import UserService

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def generate_page(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Render tweet generation page."""
    from app.main import templates

    # Check if user has DeepSeek API key
    user_service = UserService(db)
    api_key = await user_service.get_api_key(user.id, APIKeyType.DEEPSEEK)

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        "generate/index.html",
        {
            "request": request,
            "user": user,
            "has_api_key": api_key is not None and api_key.is_valid,
            "tones": [t.value for t in TweetTone],
            "default_tone": user.default_tone,
            "default_prompt": user.default_prompt_template,
            "csrf_token": csrf_token,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
    return response


@router.post("/tweet")
async def generate_tweet(
    request: Request,
    user: RateLimitedUser,
    prompt: Annotated[str, Form()],
    tone: Annotated[str, Form()] = "professional",
    instructions: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Generate a single tweet using DeepSeek API."""
    from app.main import templates

    # Get API key
    user_service = UserService(db)
    api_key = await user_service.get_decrypted_api_key(user.id, APIKeyType.DEEPSEEK)

    if not api_key:
        return RedirectResponse(
            url="/generate?error=Please+configure+your+DeepSeek+API+key+first",
            status_code=status.HTTP_302_FOUND,
        )

    deepseek_service = DeepSeekService(api_key)
    audit_service = AuditService(db)

    try:
        # Map tone string to enum
        tone_enum = TweetTone(tone)

        # Generate tweet
        generated_content = await deepseek_service.generate_tweet(
            prompt=prompt,
            tone=tone_enum,
            custom_system_prompt=user.default_prompt_template,
            instructions=instructions,
        )

        # Log audit
        from app.models.audit import AuditAction
        await audit_service.log(
            action=AuditAction.TWEET_GENERATED,
            user_id=user.id,
            resource_type="generation",
            details={"type": "single_tweet", "tone": tone},
        )
        await db.commit()

        csrf_token = generate_csrf_token()

        response = templates.TemplateResponse(
            "generate/result.html",
            {
                "request": request,
                "user": user,
                "generated_content": generated_content,
                "is_thread": False,
                "prompt": prompt,
                "tone": tone,
                "character_count": len(generated_content),
                "csrf_token": csrf_token,
            },
        )
        response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
        return response

    except DeepSeekAPIError as e:
        logger.error(
            "DeepSeek API error",
            user_id=str(user.id),
            error=str(e),
        )
        return RedirectResponse(
            url=f"/generate?error=Generation+failed:+{str(e)[:50].replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )
    finally:
        await deepseek_service.close()


@router.post("/thread")
async def generate_thread(
    request: Request,
    user: RateLimitedUser,
    prompt: Annotated[str, Form()],
    num_tweets: Annotated[int, Form()] = 3,
    tone: Annotated[str, Form()] = "professional",
    instructions: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Generate a tweet thread using DeepSeek API."""
    from app.main import templates

    # Get API key
    user_service = UserService(db)
    api_key = await user_service.get_decrypted_api_key(user.id, APIKeyType.DEEPSEEK)

    if not api_key:
        return RedirectResponse(
            url="/generate?error=Please+configure+your+DeepSeek+API+key+first",
            status_code=status.HTTP_302_FOUND,
        )

    deepseek_service = DeepSeekService(api_key)

    try:
        # Map tone string to enum
        tone_enum = TweetTone(tone)

        # Generate thread
        generated_tweets = await deepseek_service.generate_thread(
            prompt=prompt,
            num_tweets=num_tweets,
            tone=tone_enum,
            custom_system_prompt=user.default_prompt_template,
            instructions=instructions,
        )

        csrf_token = generate_csrf_token()

        response = templates.TemplateResponse(
            "generate/result.html",
            {
                "request": request,
                "user": user,
                "generated_content": "\n---\n".join(generated_tweets),
                "thread_tweets": generated_tweets,
                "is_thread": True,
                "prompt": prompt,
                "tone": tone,
                "csrf_token": csrf_token,
            },
        )
        response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
        return response

    except DeepSeekAPIError as e:
        logger.error(
            "DeepSeek API error",
            user_id=str(user.id),
            error=str(e),
        )
        return RedirectResponse(
            url=f"/generate?error=Generation+failed:+{str(e)[:50].replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )
    finally:
        await deepseek_service.close()


@router.post("/improve")
async def improve_tweet(
    request: Request,
    user: RateLimitedUser,
    content: Annotated[str, Form()],
    feedback: Annotated[Optional[str], Form()] = None,
    tone: Annotated[str, Form()] = "professional",
    db: AsyncSession = Depends(get_db),
):
    """Improve an existing tweet using DeepSeek API."""
    from app.main import templates

    # Get API key
    user_service = UserService(db)
    api_key = await user_service.get_decrypted_api_key(user.id, APIKeyType.DEEPSEEK)

    if not api_key:
        return RedirectResponse(
            url="/generate?error=Please+configure+your+DeepSeek+API+key+first",
            status_code=status.HTTP_302_FOUND,
        )

    deepseek_service = DeepSeekService(api_key)

    try:
        # Map tone string to enum
        tone_enum = TweetTone(tone)

        # Improve tweet
        improved_content = await deepseek_service.improve_tweet(
            original_tweet=content,
            tone=tone_enum,
            feedback=feedback,
        )

        csrf_token = generate_csrf_token()

        response = templates.TemplateResponse(
            "generate/result.html",
            {
                "request": request,
                "user": user,
                "generated_content": improved_content,
                "original_content": content,
                "is_thread": False,
                "is_improvement": True,
                "tone": tone,
                "character_count": len(improved_content),
                "csrf_token": csrf_token,
            },
        )
        response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="strict")
        return response

    except DeepSeekAPIError as e:
        logger.error(
            "DeepSeek API error",
            user_id=str(user.id),
            error=str(e),
        )
        return RedirectResponse(
            url=f"/generate?error=Improvement+failed:+{str(e)[:50].replace(' ', '+')}",
            status_code=status.HTTP_302_FOUND,
        )
    finally:
        await deepseek_service.close()


@router.post("/save-draft")
async def save_as_draft(
    request: Request,
    user: CurrentUser,
    content: Annotated[str, Form()],
    is_thread: Annotated[bool, Form()] = False,
    thread_content: Annotated[Optional[str], Form()] = None,
    prompt_used: Annotated[Optional[str], Form()] = None,
    tone_used: Annotated[Optional[str], Form()] = None,
    db: AsyncSession = Depends(get_db),
):
    """Save generated content as a draft."""
    tweet_service = TweetService(db)

    try:
        thread_contents = None
        if is_thread and thread_content:
            thread_contents = [
                t.strip() for t in thread_content.split("\n---\n") if t.strip()
            ]

        tone_enum = TweetTone(tone_used) if tone_used else None

        draft = await tweet_service.create_draft(
            user_id=user.id,
            content=content,
            is_thread=is_thread,
            thread_contents=thread_contents,
            generated_by_ai=True,
            prompt_used=prompt_used,
            tone_used=tone_enum,
        )
        await db.commit()

        logger.info("Draft saved", draft_id=str(draft.id), user_id=str(user.id))

        return RedirectResponse(
            url=f"/tweets/new?draft_id={draft.id}&success=Draft+saved",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        logger.error("Failed to save draft", error=str(e))
        return RedirectResponse(
            url="/generate?error=Failed+to+save+draft",
            status_code=status.HTTP_302_FOUND,
        )
