"""Associate requests with durable question outcomes; legacy records remain nullable."""
from alembic import op
import sqlalchemy as sa

revision = '072_question_replay'
down_revision = 'a2e2ecfa5a9c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_record', sa.Column('request_id', sa.String(128), nullable=True))
    op.add_column('chat_record', sa.Column('request_fingerprint', sa.String(64), nullable=True))
    op.add_column('chat_record', sa.Column('status', sa.String(16), nullable=True))
    op.create_unique_constraint('uq_chat_record_request', 'chat_record', ['chat_id', 'request_id'])


def downgrade():
    op.drop_constraint('uq_chat_record_request', 'chat_record', type_='unique')
    op.drop_column('chat_record', 'status')
    op.drop_column('chat_record', 'request_fingerprint')
    op.drop_column('chat_record', 'request_id')
