from setuptools import find_packages
from setuptools import setup


setup(name='mdserver',
      description='Simple metadata server',
      version='0.1',
      url='https://bitbucket.org/xchandan/md_server',
      author='Chandan Dutta Chowdhury',
      author_email='chandan.dutta.chowdhury@gmail.com',
      packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
      install_requires=[line.rstrip() for line in open('requirements.txt')],
      scripts=['bin/mdserver'],
      data_files=[
          ('/etc/mdserver', ['etc/mdserver/mdserver.conf']),
          ('/etc/default', ['etc/default/mdserver']),
          ('/etc/init.d', ['etc/init/sysv/mdserver']),
          ('/etc/systemd/system', ['etc/init/systemd/system/mdserver.service'])
          ])
