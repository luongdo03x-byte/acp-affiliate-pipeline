# Import account extensions before server.create_app() registers the shared Seeding blueprint.
from . import seeding_account_routes as _seeding_account_routes  # noqa: F401,E402
