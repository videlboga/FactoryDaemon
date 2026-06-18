"""Точка входа для CLI FactoryDaemon."""

import typer

import factorydaemon

app = typer.Typer(
    name="factorydaemon",
    help="Агентная система для управления производством",
    add_completion=False,
)


@app.command()
def version() -> None:
    """Показать версию приложения."""
    typer.echo(factorydaemon.__version__)


@app.command()
def serve() -> None:
    """Запустить HTTP API (заглушка)."""
    typer.echo("Serve command is not implemented yet.")


def main() -> None:
    """Точка входа для консольного скрипта."""
    app()


if __name__ == "__main__":
    main()
