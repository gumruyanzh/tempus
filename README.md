# Tempus - AI-Powered Tweet Scheduling SaaS

Tempus is an enterprise-grade SaaS application for scheduling and managing Twitter/X posts with AI-powered content generation using DeepSeek.

## Features

- **User Authentication**: Secure registration and login with JWT tokens stored in HttpOnly cookies
- **Twitter Integration**: OAuth 2.0 connection to post tweets directly to Twitter/X
- **AI Tweet Generation**: Generate tweets and threads using DeepSeek LLM with multiple tone options
- **Tweet Scheduling**: Schedule tweets for future posting with timezone support
- **Dashboard**: View scheduled, posted, and failed tweets with statistics
- **Admin Panel**: User management and audit log viewing for administrators
- **Enterprise Features**: Audit logging, rate limiting, soft deletes, structured logging

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy (async), Pydantic v2
- **Database**: PostgreSQL
- **Caching/Queue**: Redis, Celery
- **Frontend**: Jinja2 templates, TailwindCSS (CDN)
- **Security**: bcrypt, JWT, Fernet encryption

## Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Docker & Docker Compose (optional, recommended)

## Quick Start with Docker

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd tempus
   ```

2. **Create environment file**:
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env` with your configuration**:
   ```bash
   # Generate a secure secret key
   python -c "import secrets; print(secrets.token_urlsafe(32))"

   # Generate a Fernet encryption key
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. **Start all services**:
   ```bash
   docker-compose up -d
   ```

5. **Run database migrations**:
   ```bash
   docker-compose exec web alembic upgrade head
   ```

6. **Access the application**:
   - Web App: http://localhost:8000
   - Flower (Celery Monitor): http://localhost:5555

## Manual Installation

1. **Create virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up PostgreSQL**:
   ```bash
   createdb tempus
   ```

4. **Set up Redis**:
   ```bash
   # Install and start Redis
   redis-server
   ```

5. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

6. **Run migrations**:
   ```bash
   alembic upgrade head
   ```

7. **Start the application**:
   ```bash
   # Terminal 1: FastAPI server
   uvicorn app.main:app --reload

   # Terminal 2: Celery worker
   celery -A celery_worker.celery_app worker --loglevel=info

   # Terminal 3: Celery beat scheduler
   celery -A celery_worker.celery_app beat --loglevel=info
   ```

## Twitter (X) API Configuration

1. **Create a Twitter Developer Account**:
   - Go to https://developer.twitter.com/
   - Create a project and app

2. **Configure OAuth 2.0**:
   - Enable OAuth 2.0 in your app settings
   - Set the callback URL to: `http://localhost:8000/auth/twitter/callback`
   - Required scopes: `tweet.read`, `tweet.write`, `users.read`, `offline.access`

3. **Update `.env`**:
   ```
   TWITTER_CLIENT_ID=your-client-id
   TWITTER_CLIENT_SECRET=your-client-secret
   TWITTER_REDIRECT_URI=http://localhost:8000/auth/twitter/callback
   ```

## DeepSeek API Configuration

1. **Get API Key**:
   - Go to https://platform.deepseek.com/
   - Create an account and generate an API key

2. **Configure in App**:
   - After logging in, go to Settings
   - Enter your DeepSeek API key
   - The key is encrypted at rest using Fernet

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SECRET_KEY` | Application secret key | Yes |
| `ENCRYPTION_KEY` | Fernet key for encrypting API keys | Yes |
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `REDIS_URL` | Redis connection string | Yes |
| `JWT_SECRET_KEY` | JWT signing key | Yes |
| `TWITTER_CLIENT_ID` | Twitter OAuth client ID | Yes |
| `TWITTER_CLIENT_SECRET` | Twitter OAuth client secret | Yes |
| `TWITTER_REDIRECT_URI` | Twitter OAuth callback URL | Yes |
| `DEEPSEEK_API_BASE_URL` | DeepSeek API base URL | No |
| `LOG_LEVEL` | Logging level (INFO, DEBUG, etc.) | No |
| `LOG_FORMAT` | Log format (json, console) | No |

## Project Structure

```
tempus/
├── app/
│   ├── api/              # Route handlers
│   │   ├── admin.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── generate.py
│   │   ├── health.py
│   │   ├── settings.py
│   │   └── tweets.py
│   ├── auth/             # Authentication dependencies
│   ├── core/             # Core configuration
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── logging.py
│   │   └── security.py
│   ├── models/           # SQLAlchemy models
│   │   ├── audit.py
│   │   ├── oauth.py
│   │   ├── tweet.py
│   │   └── user.py
│   ├── services/         # Business logic
│   │   ├── audit.py
│   │   ├── auth.py
│   │   ├── deepseek.py
│   │   ├── tweet.py
│   │   ├── twitter.py
│   │   └── user.py
│   ├── tasks/            # Celery tasks
│   │   ├── celery_app.py
│   │   ├── maintenance_tasks.py
│   │   └── tweet_tasks.py
│   ├── templates/        # Jinja2 templates
│   ├── static/           # Static files
│   ├── utils/            # Utilities
│   └── main.py           # FastAPI app
├── alembic/              # Database migrations
├── celery_worker.py      # Celery entry point
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

## API Endpoints

### Health
- `GET /health` - Basic health check
- `GET /health/ready` - Readiness check with DB
- `GET /health/live` - Liveness check

### Authentication
- `GET /auth/login` - Login page
- `POST /auth/login` - Login form submission
- `GET /auth/register` - Registration page
- `POST /auth/register` - Registration form submission
- `GET /auth/logout` - Logout
- `GET /auth/twitter/connect` - Initiate Twitter OAuth
- `GET /auth/twitter/callback` - Twitter OAuth callback

### Dashboard
- `GET /dashboard` - Main dashboard
- `GET /dashboard/history` - Tweet history
- `GET /dashboard/drafts` - Saved drafts

### Tweets
- `GET /tweets/new` - New tweet form
- `POST /tweets/schedule` - Schedule a tweet
- `GET /tweets/{id}` - View tweet
- `GET /tweets/{id}/edit` - Edit tweet form
- `POST /tweets/{id}/edit` - Update tweet
- `POST /tweets/{id}/cancel` - Cancel tweet
- `POST /tweets/{id}/delete` - Delete tweet
- `POST /tweets/{id}/duplicate` - Duplicate tweet
- `POST /tweets/{id}/retry` - Retry failed tweet

### Generation
- `GET /generate` - Generation page
- `POST /generate/tweet` - Generate single tweet
- `POST /generate/thread` - Generate thread
- `POST /generate/improve` - Improve existing tweet
- `POST /generate/save-draft` - Save as draft

### Settings
- `GET /settings` - Settings page
- `POST /settings/profile` - Update profile
- `POST /settings/password` - Change password
- `POST /settings/deepseek-key` - Update API key
- `POST /settings/prompt-defaults` - Update defaults

### Admin
- `GET /admin` - Admin dashboard
- `GET /admin/users` - User list
- `GET /admin/users/{id}` - User details
- `POST /admin/users/{id}/toggle-active` - Activate/deactivate
- `POST /admin/users/{id}/toggle-role` - Change role
- `GET /admin/audit-logs` - Audit logs

## Creating an Admin User

After registration, you can promote a user to admin via the database:

```sql
UPDATE users SET role = 'ADMIN' WHERE email = 'your-email@example.com';
```

Or using the Python shell:
```python
from app.core.database import get_db_context
from app.models.user import User, UserRole
from sqlalchemy import select

async def make_admin(email: str):
    async with get_db_context() as db:
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        user = result.scalar_one()
        user.role = UserRole.ADMIN
        await db.commit()
```

## Security Considerations

1. **HTTPS**: Always use HTTPS in production
2. **Secrets**: Never commit `.env` files
3. **Cookies**: Secure cookies are enabled in production mode
4. **Rate Limiting**: Configure appropriate limits for your use case
5. **CSRF**: CSRF tokens are used for all form submissions
6. **Encryption**: API keys are encrypted at rest using Fernet

## Monitoring

- **Flower**: Celery task monitoring at `/5555`
- **Health Endpoints**: Use `/health/ready` for container orchestration
- **Structured Logging**: JSON logs in production for log aggregation

## License

MIT License

## Support

For issues and feature requests, please use the GitHub issue tracker.
