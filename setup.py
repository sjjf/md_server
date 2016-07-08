from distutils.core import setup

setup(name='mdserver',
      description='Simple metadata server',
      version='0.1',
      url='https://bitbucket.org/xchandan/md_server',
      author='Chandan Dutta Chowdhury',
      author_email='chandan.dutta.chowdhury@gmail.com',
      packages=['mdserver'],
      data_files=[('/etc/mdserver', ['etc/mdserver/mdserver.conf']),
                  ('/usr/bin', ['bin/mdserver']),
                  ('/etc/systemd/system',
                   ['etc/systemd/system/mdserver.service'])
                  ])
