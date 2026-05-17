from django.apps import AppConfig


class ClientesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'clientes'

    def ready(self):
        """Garante que a tabela TopologiaDiagrama existe ao iniciar a app."""
        try:
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS clientes_topologiadiagrama (
                        id BIGSERIAL PRIMARY KEY,
                        nome VARCHAR(255) NOT NULL DEFAULT 'Nova Topologia',
                        dados_json TEXT NOT NULL DEFAULT '{"nodes":[],"links":[]}',
                        drawio_xml TEXT NOT NULL DEFAULT '',
                        atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        cliente_id BIGINT NOT NULL REFERENCES clientes_cliente(id) ON DELETE CASCADE
                    );
                    INSERT INTO django_migrations (app, name, applied)
                    VALUES ('clientes', '0040_topologia_diagrama', NOW())
                    ON CONFLICT (app, name) DO NOTHING;
                """)
        except Exception as e:
            import logging
            logging.getLogger('clientes').warning(f'Auto-migration TopologiaDiagrama: {e}')
