from setuptools import setup, find_packages

setup(
    name="httpfrost",
    version="1.0.0",
    description="Secure LAN file sharing HTTP server with upload and selective ZIP download",
    author="James Majura",
    author_email="captainmajura@gmail.com",
    packages=find_packages(),
    python_requires='>=3.7',
    entry_points={
        'console_scripts': [
            'httpfrost=httpfrost.server:main',
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
)
