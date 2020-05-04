## Installing SpinningUp Fork 
- It's a private repo. Get access from Craig.
> `SpinningUp` uses MPI through `mpi4py`, which uses a compatible version of `ld` to link some of the `C` code. OpenMPI on Fedora gets installed to `/lib64/openmpi/bin/mpicc` but adding the library paths to `LD_LIBRARY_PATH` does not help as MPI code is best compiled with MPICC and other tools (wrappers). So the following step: 

- Need to export MPICC path with
```
export MPICC='/lib64/openmpi/bin/mpicc'
```
before running 
```
pip install -e . 
```
From SpinningUp's top-level directory.



## Installing DeepDriveZero(DDZ)
- As the README says, just need to run
```python
pip install -e .
```

## Running DDZ
- Need to run `python deepdrive_zero/player.py --intersection --no-timeout` from above the deepdrive_zero directory as there are scripts in that folder that import `deepdrive_zero`. 



## Parallelizing the env.step() method.
The current design iterates over all agents and makes blocking calls to every 
agent's step function. With multi-process approaches one common issue is to 
decide how much data we want sent to another process. Here are the attempted parallelization efforts:

### Trying multiprocessor.Pool
This does not work because Python is unable to automatically pickle the objects 
of Agent class.  

### Loop Parallelization with Numba
Numba has problems with both precise typing and pickling of classes. The 
suggested route is to do away with the Agent class and instead represent the 
agent as tuple of state information and methods that act on the state information.


### Using joblib
Joblib also uses process parallelization and eventually runs into issues with 
pickling.

### Making Agent inherit from Process
This will put a one agent per process limit.
Any other shortcomings?


### Creating an Agent Manager 
That spawns agents in a new process.
