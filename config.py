import os

class Config:
    # Environment Variables
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'production')
    DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')
    DATABASE_URI = os.getenv('DATABASE_URI')
    SECRET_KEY = os.getenv('SECRET_KEY')

    @staticmethod
    def validate():
        if not Config.DATABASE_URI:
            raise ValueError("DATABASE_URI is not set.")
        if not Config.SECRET_KEY:
            raise ValueError("SECRET_KEY is not set.")

    @classmethod
    def init_app(cls):
        cls.validate()  # Validate environment variables
        # Additional setup can be done here if needed
