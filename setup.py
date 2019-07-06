from setuptools import find_packages
from setuptools import setup


setup(name='mdserver',
      description='Simple metadata server',
      version='0.5.0',
      url='https://github.com/sjjf/md_server',
      author='Simon Fowler',
      author_email='simon.fowler@anu.edu.au',
      license='MIT',
      license_file='LICENSE.txt',
      packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
      install_requires=[line.rstrip() for line in open('requirements.txt')],
      test_suite='tests.test_all',
      scripts=[],
      entry_points={
          'console_scripts': [
              'mdserver = mdserver.server:main',
          ],
      },
      data_files=[
          ('/etc/mdserver', ['etc/mdserver/mdserver.conf']),
          ('/etc/default', ['etc/default/mdserver']),
          ('/etc/init.d', ['etc/init/sysv/mdserver']),
          ('/etc/systemd/system', ['etc/init/systemd/system/mdserver.service'])
          ])
