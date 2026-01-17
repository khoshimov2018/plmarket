"""
Configuration management for the Polymarket Esports Arbitrage Bot.
Uses Pydantic for validation and type safety.
"""

from pathlib import Path
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PolymarketConfig(BaseSettings):
    """Polymarket API configuration."""
    
    # Private key is optional for paper trading mode
    # For live trading, you MUST provide a valid private key
    private_key: str = Field("", alias="POLYMARKET_PRIVATE_KEY")
    api_key: str = Field("", alias="POLYMARKET_API_KEY")
    api_secret: str = Field("", alias="POLYMARKET_API_SECRET")
    api_passphrase: str = Field("", alias="POLYMARKET_API_PASSPHRASE")
    chain_id: int = Field(137, alias="CHAIN_ID")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    def is_configured(self) -> bool:
        """Check if Polymarket credentials are configured for live trading."""
        return bool(self.private_key and self.api_key and self.api_secret)


class EsportsDataConfig(BaseSettings):
    """Esports data source configuration."""
    
    pandascore_api_key: str = Field("", alias="PANDASCORE_API_KEY")
    grid_api_key: str = Field("", alias="GRID_API_KEY")
    stratz_api_key: str = Field("", alias="STRATZ_API_KEY")
    opendota_api_key: str = Field("", alias="OPENDOTA_API_KEY")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class CryptoDataConfig(BaseSettings):
    """Crypto data source configuration (Binance)."""
    
    binance_api_key: str = Field("", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field("", alias="BINANCE_API_SECRET")
    
    # Trading pairs to monitor for Polymarket crypto markets
    # These are pairs where Polymarket has price prediction markets
    crypto_pairs: str = Field("BTCUSDT,ETHUSDT,SOLUSDT", alias="CRYPTO_PAIRS")
    
    # Enable crypto arbitrage module
    enable_crypto: bool = Field(True, alias="ENABLE_CRYPTO")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    def is_configured(self) -> bool:
        """Check if Binance credentials are configured."""
        return bool(self.binance_api_key and self.binance_api_secret)


class TradingConfig(BaseSettings):
    """Trading parameters configuration."""
    
    initial_capital: float = Field(900.0, alias="INITIAL_CAPITAL")
    max_position_size_pct: float = Field(0.1, alias="MAX_POSITION_SIZE_PCT")
    min_edge_threshold: float = Field(0.02, alias="MIN_EDGE_THRESHOLD")
    max_slippage: float = Field(0.01, alias="MAX_SLIPPAGE")
    stop_loss_pct: float = Field(0.05, alias="STOP_LOSS_PCT")
    take_profit_pct: float = Field(0.10, alias="TAKE_PROFIT_PCT")
    max_concurrent_positions: int = Field(5, alias="MAX_CONCURRENT_POSITIONS")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    @field_validator("max_position_size_pct", "min_edge_threshold", "max_slippage")
    @classmethod
    def validate_percentage(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("Percentage must be between 0.0 and 1.0")
        return v


class RiskConfig(BaseSettings):
    """Risk management configuration."""
    
    daily_loss_limit_pct: float = Field(0.15, alias="DAILY_LOSS_LIMIT_PCT")
    max_drawdown_pct: float = Field(0.25, alias="MAX_DRAWDOWN_PCT")
    loss_cooldown_seconds: int = Field(30, alias="LOSS_COOLDOWN_SECONDS")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class ExecutionConfig(BaseSettings):
    """Order execution configuration."""
    
    min_execution_delay_ms: int = Field(50, alias="MIN_EXECUTION_DELAY_MS")
    max_execution_delay_ms: int = Field(200, alias="MAX_EXECUTION_DELAY_MS")
    price_check_interval_ms: int = Field(500, alias="PRICE_CHECK_INTERVAL_MS")
    game_state_poll_interval_ms: int = Field(100, alias="GAME_STATE_POLL_INTERVAL_MS")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class MonitoringConfig(BaseSettings):
    """Monitoring and notification configuration."""
    
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    enable_notifications: bool = Field(False, alias="ENABLE_NOTIFICATIONS")
    discord_webhook_url: str = Field("", alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field("", alias="TELEGRAM_CHAT_ID")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class DatabaseConfig(BaseSettings):
    """Database configuration."""
    
    database_path: Path = Field(Path("./data/trades.db"), alias="DATABASE_PATH")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class DevelopmentConfig(BaseSettings):
    """Development and testing configuration."""
    
    paper_trading: bool = Field(True, alias="PAPER_TRADING")
    debug_mode: bool = Field(False, alias="DEBUG_MODE")
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class BotConfig:
    """Master configuration class that aggregates all config sections."""
    
    def __init__(self):
        self.polymarket = PolymarketConfig()
        self.esports = EsportsDataConfig()
        self.crypto = CryptoDataConfig()
        self.trading = TradingConfig()
        self.risk = RiskConfig()
        self.execution = ExecutionConfig()
        self.monitoring = MonitoringConfig()
        self.database = DatabaseConfig()
        self.development = DevelopmentConfig()
        
        # Ensure data directory exists
        self.database.database_path.parent.mkdir(parents=True, exist_ok=True)
    
    @property
    def is_paper_trading(self) -> bool:
        return self.development.paper_trading
    
    @property
    def is_debug(self) -> bool:
        return self.development.debug_mode


# Global config instance
_config: Optional[BotConfig] = None


def get_config() -> BotConfig:
    """Get or create the global configuration instance."""
    global _config
    if _config is None:
        _config = BotConfig()
    return _config


def reload_config() -> BotConfig:
    """Force reload configuration from environment."""
    global _config
    _config = BotConfig()
    return _config




