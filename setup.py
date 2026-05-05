from setuptools import setup, find_packages

setup(
    name="ticktick-companion",
    version="0.1.0",
    description="Companion CLI, MCP server, and dashboard for TickTick task management",
    author="Jaesung Park",
    author_email="parkjs814@gmail.com",
    url="https://github.com/parkjs814/ticktick-mcp",
    packages=find_packages(),
    install_requires=[
        "mcp[cli]>=1.2.0,<2.0.0",
        "python-dotenv>=1.0.0,<2.0.0",
        "requests>=2.30.0,<3.0.0",
        "flask>=3.0.0,<4.0.0",
    ],
    python_requires=">=3.10",
    include_package_data=True,
    package_data={
        "ticktick_companion.dashboard": ["templates/*.html", "static/*"],
    },
    entry_points={
        "console_scripts": [
            "ticktick-companion=ticktick_companion.cli:main",
            "ticktick-companion-dashboard=ticktick_companion.dashboard.app:main",
            "ticktick-auth=ticktick_companion.api.oauth:main",
            # Backward-compatible aliases for existing MCP/dashboard configs.
            "ticktick-mcp=ticktick_companion.cli:main",
            "ticktick-dashboard=ticktick_companion.dashboard.app:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
