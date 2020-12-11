from setuptools import setup
import sphinxcontrib.kissapi as kissapi

with open('requirements.txt', 'r') as f:
    reqs = f.read().strip().splitlines()
with open('README.rst', 'r', encoding="utf-8") as f:
    long_description = f.read()

setup(
    name='sphinxcontrib-kissapi',
    version=kissapi.__version__,
    url='https://github.com/Azmisov/sphinxcontrib-kissapi',
    license='MIT',
    author='Isaac Nygaard',
    author_email='ozixtheorange@gmail.com',
    description="Simple and flexible python API documentation generation plugin for Sphinx",
    long_description=long_description,
    long_description_content_type="text/x-rst",
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