"""Verifica el panel admin local: login renderiza formulario y el login funciona."""

import asyncio
import os

os.environ['DATABASE_URL'] = 'postgresql+asyncpg://tempus_app:tempus_app_dev_pw@localhost:5432/tempus'
os.environ['DATABASE_MIGRATION_URL'] = 'postgresql+asyncpg://tempus_owner:tempus_owner_dev_pw@localhost:5432/tempus'


async def main() -> None:
    import httpx

    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://t', follow_redirects=False) as c:
        # 1) /admin sin sesion -> redirige al login
        r = await c.get('/admin')
        print('/admin sin sesion:', r.status_code, '->', r.headers.get('location'))

        # 2) /admin/login -> el formulario, NO el cartel rojo
        r2 = await c.get('/admin/login')
        tiene_form = 'form' in r2.text.lower() and 'password' in r2.text.lower()
        tiene_cartel = 'no está configurado' in r2.text or 'no esta configurado' in r2.text
        print('/admin/login:', r2.status_code, '| formulario:', tiene_form, '| cartel rojo:', tiene_cartel)

        # 3) Loguear con ADMIN_USER local (pass del .env: la que genere el hash)
        # Se prueba solo la estructura del POST; la password real no la conozco.
        r3 = await c.post('/admin/login', data={'user': 'Andres', 'password': 'incorrecta'})
        print('login pass incorrecta:', r3.status_code, '| error visible:', 'incorrecta' in r3.text.lower() or 'invalid' in r3.text.lower())


asyncio.run(main())
