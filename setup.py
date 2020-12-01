from setuptools import setup
import sphinxcontrib.kissapi as kissapi

reqs = open('requirements.txt', 'r').read().strip().splitlines()

setup(
    name='sphinxcontrib-kissapi',
    version=kissapi.__version__,
    url='https://github.com/Azmisov/sphinxcontrib-kissapi',
    license='MIT',
    author='Isaac Nygaard',
    author_email='ozixtheorange@gmail.com',
    description="Simple and flexible python API documentation generation plugin for Sphinx",
    keywords="sphinx automatic api generation documentation",
    zip_safe=False,
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Topic :: Documentation',
        'Topic :: Utilities',
        'Framework :: Sphinx',
        'Framework :: Sphinx :: Extension',
    ],
    platforms='any',
    packages=["sphinxcontrib.kissapi"],
    package_data={
        "sphinxcontrib.kissapi":["def_templates/*.rst"]
    },
    install_requires=reqs,
    namespace_packages=['sphinxcontrib']
)