from distutils.core import setup

setup(name='mdserver',
      description='Simple metadata server',
      author='Chandan Dutta Chowdhury',
      author_email='chandan.dutta.chowdhury@gmail.com',
      packages=['mdserver'],
      package_dir={'etc': 'etc'},
      package_data={'etc': ['etc/mdserver/*']})
