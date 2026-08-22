import logging
import os
from typing import Annotated, Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel, Field

from app.DB.main import SessionLocal
from app.DB import members as member_queries
from app.DB.wallet import (
    get_or_create_member_profile,
    get_public_profile_by_uuid,
    is_member_admin,
    update_member_profile,
)
from app.helpers import authenticated_guard, resolve_member
from app.wallet_signer import generate_apple_pkpass, generate_google_wallet_pass_url

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Request & Response Models
# =============================================================================


class SocialLinkItem(BaseModel):
    id: str
    platform: str
    url: str
    label: Optional[str] = None


class ProfileVisibility(BaseModel):
    showPhone: Optional[bool] = False
    showEmail: Optional[bool] = False
    showAcademic: Optional[bool] = True
    showBio: Optional[bool] = True


class UpdateWalletMePayload(BaseModel):
    custom_name: Optional[str] = Field(default=None, description="Preferred display name on card")
    theme_id: Optional[str] = Field(default=None, description="Theme ID (gdg-blue, gdg-red, gdg-gold-admin)")
    name_language: Optional[str] = Field(default=None, description="Name language label preference: ar or en")
    user_status: Optional[str] = Field(default=None, description="student or graduate")
    education_level: Optional[str] = Field(default=None, description="university or highschool")
    institution: Optional[str] = Field(default=None, description="University or school name")
    major: Optional[str] = Field(default=None, description="Major / field of study")
    study_year_or_level: Optional[str] = Field(default=None, description="Level e.g. المستوى 7")
    bio: Optional[str] = Field(default=None, description="Short member bio")
    social_links: Optional[List[SocialLinkItem]] = Field(default=None, description="List of social links")
    visibility: Optional[ProfileVisibility] = Field(default=None, description="Visibility settings")
    email: Optional[str] = Field(default=None, description="Updated contact email")
    phone_number: Optional[str] = Field(default=None, description="Updated phone number")


# =============================================================================
# Helpers
# =============================================================================


def _resolve_pass_card_data(session, credentials, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Builds Apple/Google Wallet pass data from the authenticated member's DB
    record (the same identity `resolve_member` resolves for every other
    endpoint). ``payload`` may only override the cosmetic ``themeId`` for a
    live preview - it can never substitute for a real member.
    """
    card_data = dict(payload or {})
    member = resolve_member(session, credentials)
    profile = get_or_create_member_profile(session, member.id)
    session.commit()

    is_admin = is_member_admin(member)
    theme_id = profile.theme_id or card_data.get("themeId", "gdg-blue")
    if theme_id == "gdg-gold-admin" and not is_admin:
        theme_id = "gdg-blue"

    effective_name = profile.custom_name or member.name

    return {
        "uuid": profile.uuid,
        "fullName": effective_name,
        "uniId": member.uni_id,
        "email": member.email or "",
        "phone": member.phone_number or "",
        "uniCollege": profile.institution or member.uni_college or "جامعة القصيم",
        "major": profile.major or member.uni_college or "علوم حاسب",
        "userStatus": profile.user_status or "student",
        "educationLevel": profile.education_level or "university",
        "studyYearOrLevel": profile.study_year_or_level or "",
        "themeId": theme_id,
        "nameLanguage": "ar",
        "isAdmin": is_admin,
    }


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/me", summary="Get authenticated member wallet data and profile")
def get_wallet_me(credentials=Depends(authenticated_guard)):
    """
    Returns the authenticated member's core info, role permissions, and MemberProfiles settings.

    Resolves the caller through the same ``resolve_member`` identity path as
    ``/members/me`` - by now onboarding has already created their ``Members``
    row, so a 404 here means something upstream is broken, not a normal case
    to paper over with fabricated data.
    """
    with SessionLocal() as session:
        member = resolve_member(session, credentials)
        profile = get_or_create_member_profile(session, member.id)
        session.commit()

        is_admin = is_member_admin(member)
        role_names = [r.role.value for r in member.role] if member.role else []

        effective_name = profile.custom_name or member.name
        effective_institution = profile.institution or member.uni_college or "جامعة القصيم"
        effective_major = profile.major or member.uni_college or "علوم حاسب"
        effective_level = profile.study_year_or_level or (
            f"المستوى {member.uni_level}" if member.uni_level else "عضو مجتمع GDG"
        )

        return {
            "member_id": member.id,
            "name": effective_name,
            "official_name": member.name,
            "custom_name": profile.custom_name,
            "uni_id": member.uni_id,
            "email": member.email,
            "phone_number": member.phone_number,
            "gender": member.gender.value if hasattr(member.gender, "value") else str(member.gender),
            "uni_level": member.uni_level,
            "uni_college": member.uni_college,
            "is_admin": is_admin,
            "roles": role_names,
            "profile": {
                "uuid": profile.uuid,
                "custom_name": profile.custom_name,
                "theme_id": profile.theme_id,
                "name_language": "ar",
                "user_status": profile.user_status or "student",
                "education_level": profile.education_level or "university",
                "institution": effective_institution,
                "major": effective_major,
                "study_year_or_level": effective_level,
                "bio": profile.bio or "",
                "social_links": profile.social_links or [],
                "visibility": profile.visibility
                or {"showPhone": False, "showEmail": False, "showAcademic": True, "showBio": True},
                "created_at": profile.created_at.isoformat() if profile.created_at else None,
                "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            },
        }


@router.put("/me", summary="Update member wallet profile settings")
@router.patch("/me", summary="Update member wallet profile settings")
def update_wallet_me(payload: UpdateWalletMePayload, credentials=Depends(authenticated_guard)):
    """
    Updates the authenticated member's profile and academic fields (custom_name, theme_id, name_language, user_status, education_level, institution, major, study_year_or_level, bio, social_links, visibility).
    Enforces server-side admin role check if gold card (gdg-gold-admin) is requested.
    """
    with SessionLocal() as session:
        member = resolve_member(session, credentials)
        is_admin = is_member_admin(member)

        # Check theme authorization
        if payload.theme_id == "gdg-gold-admin" and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized: Gold Leadership Card is restricted to GDG Administrators and Board Members.",
            )

        social_links_dict = (
            [item.model_dump() for item in payload.social_links] if payload.social_links is not None else None
        )
        visibility_dict = payload.visibility.model_dump() if payload.visibility is not None else None

        if payload.email is not None:
            existing_by_email = member_queries.get_member_by_email_or_none(session, str(payload.email))
            if existing_by_email and existing_by_email.id != member.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Member with email {payload.email} already exists",
                )
            member.email = str(payload.email)
        if payload.phone_number is not None:
            member.phone_number = payload.phone_number.strip() or None

        updated_profile = update_member_profile(
            session=session,
            member_id=member.id,
            custom_name=payload.custom_name,
            theme_id=payload.theme_id,
            name_language=payload.name_language,
            user_status=payload.user_status,
            education_level=payload.education_level,
            institution=payload.institution,
            major=payload.major,
            study_year_or_level=payload.study_year_or_level,
            bio=payload.bio,
            social_links=social_links_dict,
            visibility=visibility_dict,
        )
        session.commit()

        effective_name = updated_profile.custom_name or member.name

        return {
            "success": True,
            "name": effective_name,
            "email": member.email,
            "phone_number": member.phone_number,
            "profile": {
                "uuid": updated_profile.uuid,
                "custom_name": updated_profile.custom_name,
                "theme_id": updated_profile.theme_id,
                "name_language": updated_profile.name_language.value
                if hasattr(updated_profile.name_language, "value")
                else str(updated_profile.name_language),
                "user_status": updated_profile.user_status,
                "education_level": updated_profile.education_level,
                "institution": updated_profile.institution,
                "major": updated_profile.major,
                "study_year_or_level": updated_profile.study_year_or_level,
                "bio": updated_profile.bio or "",
                "social_links": updated_profile.social_links or [],
                "visibility": updated_profile.visibility,
                "updated_at": updated_profile.updated_at.isoformat() if updated_profile.updated_at else None,
            },
        }


@router.post("/apple-pass", summary="Generate signed Apple Wallet (.pkpass) for the authenticated member")
def create_apple_wallet_pass(
    payload: Annotated[Optional[Dict[str, Any]], Body()] = None, credentials=Depends(authenticated_guard)
):
    """
    Generates and signs an official Apple Wallet .pkpass file for the authenticated,
    DB-registered member. There is no guest/anonymous pass path - a wallet pass
    always represents a real member row.
    """
    with SessionLocal() as session:
        card_data = _resolve_pass_card_data(session, credentials, payload)
    pkpass_bytes = generate_apple_pkpass(card_data)
    file_name = f"gdg-pass-{card_data['uuid'][:8]}.pkpass"

    return Response(
        content=pkpass_bytes,
        media_type="application/vnd.apple.pkpass",
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.post("/google-pass", summary="Generate signed Google Wallet save link for the authenticated member")
def create_google_wallet_pass(
    payload: Annotated[Optional[Dict[str, Any]], Body()] = None, credentials=Depends(authenticated_guard)
):
    """
    Generates a signed Google Wallet save URL for the authenticated, DB-registered member.
    """
    with SessionLocal() as session:
        card_data = _resolve_pass_card_data(session, credentials, payload)
    try:
        save_url = generate_google_wallet_pass_url(card_data)
    except ValueError as exc:
        logger.error("Google Wallet configuration error: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return {"saveUrl": save_url}


@router.get("/{uuid}", summary="Get public member profile by UUID with strict visibility filtering")
def get_public_profile(uuid: str):
    """
    Publicly accessible endpoint for /p/{uuid}.
    Returns only the permitted profile and academic fields according to the owner's visibility preferences.
    """
    with SessionLocal() as session:
        result = get_public_profile_by_uuid(session, uuid)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

        member, profile, is_admin = result

        vis = profile.visibility or {}
        show_phone = bool(vis.get("showPhone", False))
        show_email = bool(vis.get("showEmail", False))
        show_academic = bool(vis.get("showAcademic", True))
        show_bio = bool(vis.get("showBio", True))

        effective_name = profile.custom_name or member.name
        effective_institution = profile.institution or member.uni_college or "جامعة القصيم"
        effective_major = profile.major or member.uni_college or "علوم حاسب"
        effective_level = profile.study_year_or_level or (
            f"المستوى {member.uni_level}" if member.uni_level else "عضو مجتمع GDG"
        )

        return {
            "uuid": profile.uuid,
            "name": effective_name,
            "name_language": profile.name_language.value
            if hasattr(profile.name_language, "value")
            else str(profile.name_language),
            "theme_id": profile.theme_id,
            "user_status": profile.user_status or "student",
            "education_level": profile.education_level or "university",
            "institution": effective_institution if show_academic else None,
            "major": effective_major if show_academic else None,
            "study_year_or_level": effective_level if show_academic else None,
            "is_admin": is_admin,
            "bio": (profile.bio or "") if show_bio else None,
            "social_links": profile.social_links or [],
            "email": member.email if show_email else None,
            "phone": member.phone_number if show_phone else None,
            "visibility": {
                "showPhone": show_phone,
                "showEmail": show_email,
                "showAcademic": show_academic,
                "showBio": show_bio,
            },
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
        }


@router.get("/health", summary="Check Wallet Pass engine health")
async def wallet_health():
    """
    Returns the status of the Wallet signing engine and available credentials.
    """
    has_apple_p12 = bool(os.getenv("APPLE_P12_BASE64")) or bool(os.getenv("APPLE_P12_PASSWORD"))
    has_google_key = bool(os.getenv("GOOGLE_WALLET_PRIVATE_KEY"))
    return {"status": "healthy", "apple_wallet_configured": has_apple_p12, "google_wallet_configured": has_google_key}
