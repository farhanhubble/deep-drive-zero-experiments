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
