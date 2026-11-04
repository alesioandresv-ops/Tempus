"""Limpieza de reservas de JosMakeUp: deja solo la de Bruna del 2026-11-03.

Primero lista (`--listar`), luego borra (`--borrar`). El borrado respeta la RLS:
pone el GUC `app.current_business_id` en la misma transaccion que el DELETE, como
exige la politica de escritura de `bookings`. Los cascades limpian booking_events,
notification_requests y booking_payments.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

FECHA_BRUNA = "2026-11-03"
NOMBRE_BRUNA = "bruna"


async def _engine():
    url = get_settings().database_migration_url
    return create_async_engine(url, future=True)


async def listar() -> None:
    engine = await _engine()
    async with engine.connect() as conn:
        negocio = (
            await conn.execute(
                sa.text("SELECT id, name, timezone FROM businesses WHERE slug = 'josmakeup'")
            )
        ).first()
        if negocio is None:
            print("No existe el negocio josmakeup")
            return
        print(f"negocio: id={negocio[0]} name={negocio[1]!r} tz={negocio[2]}")
        bid_lista = str(negocio[0])
        # FORCE RLS aplica tambien al dueño: sin el GUC el SELECT devuelve cero
        # filas, no un error. El listado necesita el GUC igual que el borrado.
        # La conexión ya esta en transaccion (autobegin); se usa esa misma.
        await conn.execute(
            sa.text("SELECT set_config('app.current_business_id', :bid, true)"),
            {"bid": bid_lista},
        )
        filas = await conn.execute(
            sa.text(
                """
                SELECT b.id, b.local_date, b.starts_at, b.status,
                       COALESCE(b.customer_first_name, c.first_name) AS nombre,
                       c.phone_e164, s.name AS servicio
                  FROM bookings b
                  LEFT JOIN customers c ON c.id = b.customer_id
                  LEFT JOIN services s ON s.id = b.service_id
                 WHERE b.business_id = :bid
                 ORDER BY b.local_date, b.starts_at
                """
            ),
            {"bid": negocio[0]},
        )
        for f in filas:
            print(f"  {f[1]} | {f[0]} | {f[4]!r} {f[5]} | {f[3]} | {f[6]!r}")
        await conn.commit()
    await engine.dispose()


async def borrar() -> None:
    engine = await _engine()
    async with engine.connect() as conn:
        negocio = (
            await conn.execute(
                sa.text("SELECT id FROM businesses WHERE slug = 'josmakeup'")
            )
        ).first()
        if negocio is None:
            print("No existe el negocio josmakeup")
            return
        bid = str(negocio[0])
        # La conexion ya esta en transaccion (autobegin); se usa esa misma.
        trans = conn.get_transaction() or await conn.begin()
        try:
            await conn.execute(
                sa.text("SELECT set_config('app.current_business_id', :bid, true)"),
                {"bid": bid},
            )
            conservar = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT b.id
                          FROM bookings b
                          LEFT JOIN customers c ON c.id = b.customer_id
                         WHERE b.business_id = :bid
                           AND b.local_date = :fecha
                           AND lower(COALESCE(b.customer_first_name, c.first_name)) LIKE :nombre
                        """
                    ),
                    {"bid": bid, "fecha": dt.date.fromisoformat(FECHA_BRUNA), "nombre": f"%{NOMBRE_BRUNA}%"},
                )
            ).scalars().all()
            if len(conservar) != 1:
                print(f"ADVERTENCIA: encontre {len(conservar)} candidatas a conservar; no borro nada.")
                for f in conservar:
                    print(f"  candidata: {f}")
                await trans.rollback()
                return
            reserva = str(conservar[0])
            print(f"Conservo: {reserva} ({FECHA_BRUNA}, {NOMBRE_BRUNA})")
            resultado = await conn.execute(
                sa.text("DELETE FROM bookings WHERE business_id = :bid AND id <> :keep"),
                {"bid": bid, "keep": reserva},
            )
            print(f"Borradas: {resultado.rowcount}")
            restantes = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM bookings WHERE business_id = :bid"),
                    {"bid": bid},
                )
            ).scalar_one()
            print(f"Restantes: {restantes}")
            if restantes != 1:
                await trans.rollback()
                print("ADVERTENCIA: no quedo exactamente una reserva; rollback.")
                return
            await trans.commit()
            print("OK: queda solo la reserva de Bruna.")
        except Exception:
            await trans.rollback()
            raise
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--borrar", action="store_true", help="Borra (default: solo lista)")
    args = parser.parse_args()
    asyncio.run(borrar() if args.borrar else listar())
