from setuptools import setup

setup(
    cmdclass={
        "egg_info": "<make-me-fail>",
        "install": "<make-me-fail>",
        "bdist_wheel": "<make-me-fail>",
    }
)
