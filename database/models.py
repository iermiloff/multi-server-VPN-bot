import datetime
from enum import Enum
from typing import List, Optional
from sqlalchemy import String, BigInteger, DateTime, Boolean, ForeignKey, text, Table, Column, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass
# Промежуточная таблица связей "Многие-ко-Многим" между юзером и его списком спонсоров
user_partner_channels = Table(
    "user_partner_channels",
    Base.metadata,
    Column("user_id", BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True),
    Column("channel_id", BigInteger, ForeignKey("partner_channels.channel_id", ondelete="CASCADE"), primary_key=True),
)

class SubscriptionType(str, Enum):
    BASE = "base"
    PREMIUM = "premium"

class Server(Base):
    __tablename__ = "servers"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_url: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token: Mapped[str] = mapped_column(String(512), nullable=False)
    sub_port: Mapped[int] = mapped_column(default=2096)
    sub_path: Mapped[str] = mapped_column(String(100), default="sub", server_default=text("'sub'"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    
    inbounds: Mapped[List["TariffInbound"]] = relationship(back_populates="server", cascade="all, delete-orphan")

class PartnerChannel(Base):
    __tablename__ = "partner_channels"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    channel_name: Mapped[str] = mapped_column(String(150), nullable=False)
    invite_link: Mapped[str] = mapped_column(String(255), nullable=False)
    is_required: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

class User(Base):
    __tablename__ = "users"
    
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    referred_by: Mapped[int] = mapped_column(BigInteger, nullable=True)
    registered_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
    is_pro_ref: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("'0'"))
    crypto_wallet: Mapped[str] = mapped_column(String(150), nullable=True)
    partner_balance_usd: Mapped[float] = mapped_column(DateTime, default=0.0, server_default=text("'0.0'"))
    last_free_trial: Mapped[Optional[datetime.datetime]] = mapped_column(nullable=True)
    last_partner_trial: Mapped[Optional[datetime.datetime]] = mapped_column(nullable=True)
    has_active_partner_bonus: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    required_channels: Mapped[List["PartnerChannel"]] = relationship(
        secondary=lambda: user_partner_channels
    )


    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"))
    plan_type: Mapped[SubscriptionType] = mapped_column(SQLEnum(SubscriptionType))
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("'1'"))
    is_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("'0'"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    user: Mapped["User"] = relationship(back_populates="subscriptions")
    keys: Mapped[List["VPNKey"]] = relationship(back_populates="subscription", cascade="all, delete-orphan")

class VPNKey(Base):
    __tablename__ = "vpn_keys"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    
    client_email: Mapped[str] = mapped_column(String(255), nullable=False)
    sub_id: Mapped[str] = mapped_column(String(255), nullable=False)
    config_data: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
    
    subscription: Mapped["Subscription"] = relationship(back_populates="keys")
    server: Mapped["Server"] = relationship()

class TariffInbound(Base):
    __tablename__ = "tariff_inbounds"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    plan_type: Mapped[SubscriptionType] = mapped_column(String(20))
    inbound_id: Mapped[int] = mapped_column()
    protocol_name: Mapped[str] = mapped_column(String(50)) 
    port: Mapped[int] = mapped_column()
    remark: Mapped[str] = mapped_column(String(255))
    
    server: Mapped["Server"] = relationship(back_populates="inbounds")


class PaymentLog(Base):
    __tablename__ = "payment_logs"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"))
    plan_type: Mapped[str] = mapped_column(String(50))
    amount: Mapped[float] = mapped_column(BigInteger)
    paid_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

