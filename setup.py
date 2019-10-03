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
        'gs2n': ['pyyaml', 'toml']
    },
    entry_points={
        'console_scripts': [
            'gs2n=gserial.rfc2217.server:main [gs2n]'
        ]
    },
    license="GPL",
    long_description="A gevent friendly serial line",
    keywords='pyserial, gevent',
    name='gserial',
    packages=find_packages(include=['gserial']),
    url='https://github.com/tiagocoutinho/gserial',
    version='0.1.0')
