import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, TIMESTAMP, ForeignKey, Text, func
from sqlalchemy.orm import declarative_base
from pydantic import BaseModel

Base = declarative_base()

# 定义数据库模型
class SessionModelDB(Base):
    __tablename__ = "session"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(255), nullable=False)
    created_date = Column(TIMESTAMP, server_default=func.now())


class MessageDB(Base):
    __tablename__ = "message"
    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    type = Column(String(10), nullable=False)
    created_date = Column(TIMESTAMP, server_default=func.now())
    session_id = Column(Integer, ForeignKey("session.id", ondelete="CASCADE"))  # 会话删除，对话记录自动删除

# 定义 kb_files 表
class KbFile(Base):
    __tablename__ = "kb_files"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), index=True)
    path = Column(String(255))
    created_date = Column(TIMESTAMP, server_default=func.now())

# #=========================================================================

# 定义 Pydantic 模型
class SessionModel(BaseModel):
    id: int
    session_id: str
    created_date: datetime

    model_config = {
        "from_attributes": True,
        "arbitrary_types_allowed": True
    }

class Message(BaseModel):
    id: int
    think: str # 思考部分，从<think>标签里拆解出来
    final_content: str # 正式回答部分
    type: str
    created_date: datetime
    session_id: int

    model_config = {
        "from_attributes": True,
        "arbitrary_types_allowed": True
    }

# 新增 Pydantic 的 KbFile 模型
class KbFileModel(BaseModel):
    id: int
    name: str
    path: str

    model_config = {
        "from_attributes": True,
        "arbitrary_types_allowed": True
    }