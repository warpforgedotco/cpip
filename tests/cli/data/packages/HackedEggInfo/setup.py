import setuptools.command.egg_info
from setuptools import setup


class egg_info(setuptools.command.egg_info.egg_info):
    def run(self):
        setuptools.command.egg_info.egg_info.run(self)


setup(
    name="hackedegginfo",
    version="0.0.0",
    cmdclass={"egg_info": egg_info},
    zip_safe=False,
)
