from pathlib import Path

import httpx
import typer

app = typer.Typer(help="Link the current git branch to a Branchline task.")


def _branch() -> str:
    import subprocess

    result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], check=True, capture_output=True, text=True)
    return result.stdout.strip()


@app.command()
def link(
    task: int | None = typer.Option(None, "--task", "-t", help="Task number, if the branch name is not enough"),
    api: str = typer.Option("http://localhost:8000", "--api", envvar="BRANCHLINE_API"),
    token: str = typer.Option(..., "--token", envvar="BRANCHLINE_TOKEN", help="Workspace CLI token"),
) -> None:
    """Tell Branchline which task this branch belongs to."""
    branch = _branch()
    response = httpx.post(
        f"{api.rstrip('/')}/api/cli/link",
        json={"branch": branch, "task_number": task},
        headers={"x-branchline-token": token},
        timeout=20,
    )
    if response.status_code >= 400:
        typer.echo(response.text)
        raise typer.Exit(code=1)
    body = response.json()
    typer.echo(f"BL-{body['number']} {body['title']} ← {branch}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
