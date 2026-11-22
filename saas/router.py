from . import tenancy


class TenantRouter:
    """Platform tables (saas) stay in the platform database. Everything else goes to the current company's database."""
    def _db(self, model):
        if model._meta.app_label == 'saas':
            return 'default'
        return tenancy.alias()

    def db_for_read(self, model, **hints):
        return self._db(model)

    def db_for_write(self, model, **hints):
        return self._db(model)

    def allow_relation(self, a, b, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'saas':
            return db == 'default'
        return True
