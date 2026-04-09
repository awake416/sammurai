"""Application configuration loader. Reads environment variables and .env files, validates required settings, and exposes a typed Settings object."""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings and environment configuration.

    Attributes:
        APP_NAME (str): The name of the application. Defaults to "API".
            Maps to APP_NAME.
        DEBUG (bool): Flag to enable debug mode. Defaults to False.
            Maps to DEBUG.
        DATABASE_URL (str): Connection string for the primary database.
            Maps to DATABASE_URL.
        SECRET_KEY (str): Cryptographic secret for signing tokens.
            Maps to SECRET_KEY.
        ALLOWED_HOSTS (list[str]): List of allowed host headers. Defaults to ["*"].
            Maps to ALLOWED_HOSTS.
    """
    APP_NAME: str = "API"
    DEBUG: bool = False
    DATABASE_URL: str
    SECRET_KEY: str
    ALLOWED_HOSTS: list[str] = ["*"]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """
    Creates and caches a singleton instance of the application settings.

    Returns:
        Settings: The validated settings object containing application configuration.

    Raises:
        ValidationError: If required environment variables are missing or invalid.
    """
    return Settings()


def validate_config(settings: Settings) -> bool:
    """
    Performs runtime validation of the configuration object beyond basic type checking.

    Args:
        settings (Settings): The settings instance to validate.

    Returns:
        bool: True if the configuration is valid.

    Raises:
        ValueError: If specific business logic validation fails (e.g., insecure secret in production).
    """
    if not settings.DEBUG and settings.SECRET_KEY == "insecure-default":
        raise ValueError("SECRET_KEY must be changed in production environments.")
    return True