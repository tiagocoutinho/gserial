# -*- coding: utf-8 -*-

"""The setup script."""

from setuptools import setup, find_packages


setup(
    author="Tiago Coutinho",
    author_email='coutinhotiago@gmail.com',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        "Programming Language :: Python :: 2",
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ],
    description="A gevent friendly serial line",
    install_requires=['gevent', 'pyserial'],
    extras_require={
        'ser2tcp': ['pyyaml', 'toml']
    },
    entry_points={
        'console_scripts': [
            'ser2tcp=gserial.rfc2217.server:main [ser2tcp]'
        ]
    },
    license="GPL",
    long_description="A gevent friendly serial line",
    keywords='pyserial, gevent',
    name='gevent-serial',
    packages=find_packages(include=['gserial']),
    url='https://github.com/tiagocoutinho/gserial',
    version='0.2.1')
