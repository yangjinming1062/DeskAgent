# Intentionally empty — VideoGenJob lives in models.py but importing it here
# would force every registered SQLAlchemy mapper (including the
# self-referencing ``Conversation.parent`` which carries a long-standing
# ``remote_side=[id]`` typo, see conversation/models.py) to configure at
# module load time and crash. The model is registered on ModelBase.metadata
# the first time something imports ``modules.media.models``. Callers that
# need the table on ``create_all`` (main.py, tests) must import it
# explicitly.