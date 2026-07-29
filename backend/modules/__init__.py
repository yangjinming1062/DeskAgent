# Importing every domain package registers all ORM models on the shared
# ``ModelBase.registry`` so ``ModelBase.metadata`` is complete for
# ``create_all``. Add new domains here.
from . import auth
from . import companion
from . import conversation
from . import memory
from . import scheduler
from . import settings
from . import system
from . import update
from . import ws
