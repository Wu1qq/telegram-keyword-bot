from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    last_active = Column(DateTime, default=datetime.now)
    is_admin = Column(Boolean, default=False)
    settings = Column(JSON)
    subscriptions = relationship("Subscription", back_populates="user")

class Subscription(Base):
    __tablename__ = 'subscriptions'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    keyword = Column(String)
    is_regex = Column(Boolean, default=False)
    filters = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
    enabled = Column(Boolean, default=True)
    user = relationship("User", back_populates="subscriptions")

class MessageQueue(Base):
    __tablename__ = 'message_queue'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    message = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
    processed = Column(Boolean, default=False)
    error = Column(String, nullable=True)

class HealthCheck(Base):
    __tablename__ = 'health_checks'
    
    id = Column(Integer, primary_key=True)
    check_time = Column(DateTime, default=datetime.now)
    status = Column(String)
    details = Column(JSON)

def init_db(database_url):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session() 