from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.services.auth_service import AuthNotConfigured, AuthService
from app.dependencies import get_auth_service

router = APIRouter()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        token = auth_service.authenticate_user(form_data.username, form_data.password)
    except AuthNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not token:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=token, token_type="bearer")
