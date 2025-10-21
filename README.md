# Welcome to Python project

## Cookiecutter template for Python3 project

This is a [cookiecutter](https://www.cookiecutter.io/) template for generic Python3 project with preconfigured with the following tools:


| Tool                                            | Purpose                                                                |
| ----------------------------------------------- | ---------------------------------------------------------------------- |
| [uv](https://docs.astral.sh/uv/)                | Dependency management and virtual environment setup                    |
| [ruff](https://docs.astral.sh/ruff/)            | Linter for Python code                                                 |
| [pre-commit](https://pre-commit.com/)           | Framework for managing and maintaining multi-language pre-commit hooks |
| [pyright](https://github.com/microsoft/pyright) | Static type checker for Python                                         |
| [VS Code](https://code.visualstudio.com/)       | Integrated development environment with devcontainer support           |

## Setup

The easiest way to get started is use [Visual Studio Code with devcontainer](https://code.visualstudio.com/docs/devcontainers/containers)


```shell

# create virtualenv and install dependencies
uv sync
source .venv/bin/activate
pre-commit install
ruff check --fix .
pytest -v

```

## Important files

| File                                                                           | Purpose                                                                                                                                                                                     |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [main.py](main.py)                                                             | main point for FastAPI                                                                                                                                                                      |
| [settings.py](settings.py)                                                     | read settings from file specified by APP_SETTINGS_ENV or .env if it's not set. Also initializes feature flag provider.                                                                      |
| [security.py](security.py)                                                     | verify JWT token using keys from a [JWKS](https://datatracker.ietf.org/doc/html/rfc7517) endpoint                                                                                           |
| [database.py](database.py)                                                     | provides both sync and async database. available only if sqlmodel is selected. setup                                                                                                        |
| [tests/conftest.py](tests/conftest.py)                                         | [pytest](https://docs.pytest.org/en/stable/) test setup (https://docs.pytest.org/en/stable/) test setup and fixtures, including http client, test database setup and seeding with data, etc |  |
| [me/api.py](me/api.py)       | api endpoints. the router object will be included by main.py.                                                                                                                               |
| [me/models.py](me/models.py) | model classes                                                                                                                                                                               |
