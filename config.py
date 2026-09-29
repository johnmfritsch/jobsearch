import os


class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATABASE = os.path.join(BASE_DIR, 'data', 'jobsearch_dev.db')
    URL_PREFIX = os.environ.get('URL_PREFIX', '/JobSearch_dev')
    DEBUG = False

    @staticmethod
    def get_config():
        env = os.environ.get('JOBSEARCH_ENV', 'development')
        if env == 'production':
            return ProductionConfig()
        return DevelopmentConfig()


class DevelopmentConfig(Config):
    ENV = 'development'
    DEBUG = True
    URL_PREFIX = '/JobSearch_dev'
    DATABASE = os.path.join(Config.BASE_DIR, 'data', 'jobsearch_dev.db')


class ProductionConfig(Config):
    ENV = 'production'
    DEBUG = False
    URL_PREFIX = '/JobSearch'
    DATABASE = os.path.join(Config.BASE_DIR, 'data', 'jobsearch.db')
