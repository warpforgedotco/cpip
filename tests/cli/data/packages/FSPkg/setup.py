from setuptools import find_packages, setup

version = "0.1dev"

setup(
    name="FSPkg",
    version=version,
    description="File system test package",
    long_description="""\
File system test package""",
    classifiers=[],
    keywords="kpip tests",
    author="kpip",
    author_email="kpip@openplans.org",
    url="http://kpip.openplans.org",
    license="",
    packages=find_packages(exclude=["ez_setup", "examples", "tests"]),
    include_package_data=True,
    zip_safe=False,
    install_requires=[
    ],
    entry_points="""
      # -*- Entry points: -*-
      """,
)
