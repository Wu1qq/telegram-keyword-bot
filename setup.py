from setuptools import setup, find_packages
import os

def read_version():
    with open('VERSION') as f:
        return f.read().strip()

def read_requirements():
    with open('requirements.txt') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name='telegram-keyword-bot',
    version=read_version(),
    description='Telegram 关键词提醒机器人',
    author='Your Name',
    author_email='your.email@example.com',
    packages=find_packages(),
    install_requires=read_requirements(),
    python_requires='>=3.9',
    entry_points={
        'console_scripts': [
            'keyword-bot=main:main',
        ],
    },
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
    ],
) 