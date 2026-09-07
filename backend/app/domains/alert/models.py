from sqlalchemy import Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String)
    time: Mapped[str] = mapped_column(String)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    demo: Mapped[bool] = mapped_column(Boolean, default=True)
    mission_id: Mapped[str | None] = mapped_column(String, nullable=True)
