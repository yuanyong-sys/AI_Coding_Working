from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Drone(Base):
    __tablename__ = "drone"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    battery: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task: Mapped[str] = mapped_column(String)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    demo: Mapped[bool] = mapped_column(Boolean, default=True)
