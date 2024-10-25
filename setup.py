from setuptools import setup

setup(
    name="lambda-cli",
    version="0.1.0",
    py_modules=["lambda_cli"],
    install_requires=[
        "click>=8.0.0",
        "watchdog>=2.1.0",
        "PyYAML>=5.4.1",
        "requests>=2.25.0"
    ],
    entry_points={
        "console_scripts": [
            "lambda-cli=lambda_cli:cli"
        ]
    },
)
