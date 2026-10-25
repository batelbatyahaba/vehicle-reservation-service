import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        enum.Enum: Enum(enum.Enum, values_callable=lambda e: [m.value for m in e]),
    }
