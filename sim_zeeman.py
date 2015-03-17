# Zeeman Flyer
#
#  # Introduction
#    
#	This is a python wrapper for propagator_particle.c, the library used
#	for efficient propagation of particles through the zeeman decelerator.
#	The wrapper is responsible for
#		- reading of settings from the config file
#		- creating initial positions and velocities for the particle bunch
#		- loading magnetic field values from disk
#		- passing field values and parameters to the propagator
#		  (with all memory management being done in python)
#		- starting the simulation, and providing an interface to the results
#  
# @author Atreju Tauschinsky
# @copyright Copyright 2014 University of Oxford.



import numpy as np 								# used for numeric arrays, and passing data to c library 
from numpy import sqrt, pi 						# shorthand form for these functions
import time 									# only used to time execution
from matplotlib import pyplot as plt 			# only used if executed as standalone app, to display simulation results
import os, sys									# used for compilation of propagator library
from subprocess import call 					# also used for compilation
from ConfigParser import SafeConfigParser 		# reading config file
import ConfigParser
import logging
import sys
import os

import ctypes 									# used to interface with the c library (propagator_particle.c)
from ctypes import c_double, c_uint, c_int 		# shorthand form for data types
c_double_p = ctypes.POINTER(c_double)			# pointer type

np.random.seed(1)								# initialize random number generator

kB = 1.3806504E-23								# Boltzmann constant (in J/K)
muB = 9.2740154E-24 							# Bohr magneton in J/T
HBAR = 1.054571628E-34 							# Planck constant (in Js)
A = 1420405751.768*2*pi/HBAR 					# in 1/((s^2)*J)


class ZeemanFlyer(object):
	def __init__(self, verbose=True):
		self.verbose = verbose
		
		# create dictionaries for final results
		self.finalPositions = {}
		self.finalVelocities = {}
		self.finalTimes = {}

		self.localdir = os.path.dirname(os.path.realpath(__file__)) + '/'
		localdir = self.localdir
		target = 'propagator_particle'
		
		# load C library
		# and recompile if necessary
		if sys.platform.startswith('linux'):
			compiler = 'gcc'
			commonopts = ['-c', '-fPIC', '-Ofast', '-march=native', '-std=c99', '-fno-exceptions', '-fomit-frame-pointer']
			extension = '.so'
		elif sys.platform == 'win32':
			commonopts = ['-c', '-Ofast', '-march=native', '-std=c99', '-fno-exceptions', '-fomit-frame-pointer']
			compiler = 'C:\\MinGW\\bin\\gcc'
			extension = '.dll'
		else:
			raise RuntimeError('Platform not supported!')


		libpath = localdir + target + extension

		if not os.path.exists(libpath) or os.stat(localdir + target + '.c').st_mtime > os.stat(libpath).st_mtime: # we need to recompile
			from subprocess import call
			# include branch prediction generation. compile final version with only -fprofile-use
			profcommand = [compiler, target + '.c']
			profcommand[1:1] = commonopts
	
			print
			print
			print'==================================='
			print'compilation target: ', target
			call(profcommand, cwd=localdir)
			call([compiler, '-shared', target + '.o', '-o', target + extension], cwd=localdir)
			print'COMPILATION: PROFILING RUN'
			print'==================================='
			print
			print
		elif self.verbose:
			logging.info('library up to date, not recompiling field accelerator')

		
		# define interface to propagator library
		self.prop = ctypes.cdll.LoadLibrary(libpath)
		self.prop.setSynchronousParticle.argtypes = [c_double, c_double_p, c_double_p]
		self.prop.setSynchronousParticle.restype = None
		self.prop.setBFields.argtypes = [c_double_p, c_double_p, c_double_p, c_double_p, c_double, c_double, c_double, c_int, c_int, c_int]
		self.prop.setBFields.restype = None
		self.prop.setCoils.argtypes = [c_double_p, c_double, c_double, c_int]
		self.prop.setCoils.restype = None
		self.prop.setSkimmer.argtypes = [c_double, c_double, c_double, c_double]
		self.prop.setSkimmer.restype = None
		self.prop.doPropagate.argtypes = [c_double_p, c_double_p, c_double_p, c_int, c_int]
		self.prop.doPropagate.restype = None
		self.prop.setTimingParameters.argtypes = [c_double, c_double, c_double, c_double, c_double, c_double]
		self.prop.setTimingParameters.restype = None
		self.prop.calculateCoilSwitching.argtypes = [c_double, c_double, c_double_p, c_double_p, c_double_p, c_double_p]
		self.prop.calculateCoilSwitching.restype = int
		self.prop.precalculateCurrents.argtypes = [c_double_p, c_double_p]
		self.prop.precalculateCurrents.restype = int
		self.prop.setPropagationParameters.argtypes = [c_double, c_double, c_int, c_int]
		self.prop.setPropagationParameters.restype = None
		self.prop.overwriteCoils.argtypes = [c_double_p, c_double_p]
		self.prop.overwriteCoils.restype = None
	
	def loadParameters(self, config_file):
		# function to load parameters from file, and make them easily accessible to the class
		def configToDict(items):
			# sub-function turning a set of config entries to a dict, 
			# automatically converting strings to numbers where possible
			d = {}						# initialize empty dict
			for k, v in items:			# traverse all settings
				try:
					d[k] = eval(v)		# try to evaluate (essentially turning strings to numbers, but allowing things like multiplication in the config file)
				except (ValueError, NameError):		# if this goes wrong for some reason we simply keep this entry as a string
					logging.error('Could not parse option "%s", keeping value "%s" as string' % (str(k), str(v)))
					d[k] = v
			return d
		

		config = SafeConfigParser()
		config.optionxform = lambda option : option 						# no processing in parser, in particular no change of capitalisation
		logging.debug('Reading input from %s' % config_file)
		config.read(config_file)									# read config
		try:
			self.particleProps = configToDict(config.items('PARTICLE'))			# here we read the different sections, turning the entries from each section 
			self.bunchProps = configToDict(config.items('BUNCH'))				# into a dictionary that we can easily access
			self.propagationProps = configToDict(config.items('PROPAGATION'))
			self.coilProps = configToDict(config.items('COILS'))
			self.skimmerProps = configToDict(config.items('SKIMMER'))
			self.detectionProps = configToDict(config.items('DETECTION'))
			self.optimiserProps = configToDict(config.items('OPTIMISER'))
		except ConfigParser.NoSectionError as e:
			logging.critical('Input file does not contain a section named %s' % e.section)
			sys.exit(1)


	
	def addParticles(self, includeSyn=True, checkSkimmer=False, NParticlesOverride = None):
		""" Add particles with position and velocity spread given by settings,
		create random initial positions and velocities and save in class
		variables initialPositions and initialVelocities. The number generated
		is in the class dict bunchProps, or NParticlesOverride if this is not
		None.

		Args:
			includeSyn: Optional, if True, first particle in arrays will be
				the synchronous particle
			checkSkimmer: Optional, if True discard particles that would hit
				skimmer diameter.
			NParticlesOverride -- Optional, specify number of particles to
				generate.
		"""

		if NParticlesOverride is not None:
			self.bunchProps['NParticles'] = NParticlesOverride		# allow manually overriding the particle number specified in the config
		
		nGenerated = 0												# keep track of total number of generated particle
		nGeneratedGood = 0 											# number of particles passing through the skimmer
		
		# make the parameters used here available in shorthand
		nParticles = self.bunchProps['NParticles']
		v0 = self.bunchProps['v0']
		x0 = self.bunchProps['x0']
		radius = self.bunchProps['radius']
		length = self.bunchProps['length'] # for metastables this is the length of the egun pulse (?)
		TRadial = self.bunchProps['TRadial']
		TLong = self.bunchProps['TLong']
		mass = self.particleProps['mass']
		skimmerDist = self.skimmerProps['position']
		skimmerRadius = self.skimmerProps['radius']
		egunPulseDuration = self.bunchProps['egunPulseDuration']
		useEGun = self.bunchProps['useEGun']
		
		if includeSyn:
			# if includeSyn == True, the first particle in the array
			# will be the synchronous particle as given in the config file
			initialPositions = np.array([x0])
			initialVelocities = np.array([v0])
			nGenerated += 1
			nGeneratedGood += 1
		else:
			# otherwise we still have to initialise the arrays, but they are empty now (right shape only).
			initialPositions = np.zeros((0, 3))
			initialVelocities = np.zeros((0, 3))
		
		while nGeneratedGood < nParticles:		
			# keep going as long as we don't have as many good particles as we
			# need we'll create the difference between the number of particles
			# we need and the number of particles we have.
			nParticlesToSim = nParticles - nGeneratedGood			
			# (a) Generate positions from a random uniform distribution within
			# a cylinder r0 and phi0 span up a disk; z0 gives the height.
			r0_rnd = sqrt(np.random.uniform(0, radius, nParticlesToSim))*sqrt(radius)
			phi0_rnd = np.random.uniform(0, 2*pi, nParticlesToSim)
			
			# transformation polar coordinates <--> cartesian coordinates
			if useEGun:
				x0_rnd = np.random.uniform(-length/2, length/2, nParticlesToSim)
				z0_rnd = r0_rnd*np.cos(phi0_rnd)
			else:
				x0_rnd = r0_rnd*np.cos(phi0_rnd)
				z0_rnd = 5. + np.random.uniform(-length/2, length/2, nParticlesToSim)
			y0_rnd = r0_rnd*np.sin(phi0_rnd)
			
			
			# (b) Generate velocities as normally distributed random numbers if
			# you want to generate normally distributed vx-vy random numbers
			# that are centered at vx = 0 mm/mus and vy = 0 mm/mus, use
			# bivar_rnd = 1 else use bivar_rnd = 0
			sigmavr0 = sqrt(kB*TRadial/mass)/1000 # standard deviation self.vr0 component
			
			# normally distributed random numbers centered at 0 mm/mus
			# generate bi(multi)variate Gaussian data for vx and vy
			# rand_data = mvnrnd(mu, sigma,num of data)
			muvr = [0, 0] # mean values centered around 0 mm/mus
			# sigma1 = [1 0  # covariance matrix, diagonals = variances of each variable,
			#          0 1]  # off-diagonals = covariances between the variables
			# if no correlation, then off-diagonals = 0 and Sigma can also be written as a row array
			SigmaM = [[sigmavr0**2, 0], [0, sigmavr0**2]]
			vx0_rnd, vy0_rnd = np.random.multivariate_normal(muvr, SigmaM, [nParticlesToSim]).T
			
			sigmavz0 = sqrt(kB*TLong/mass)/1000 # standard deviation vz0 component
			vz0_rnd = np.random.normal(v0[2], sigmavz0, nParticlesToSim)

			if useEGun:
				t_init = np.random.uniform(0, egunPulseDuration, nParticlesToSim)
				# t_init = np.linspace(-10, 10, nParticlesToSim)
				x0_rnd -= vx0_rnd*t_init
				y0_rnd -= vy0_rnd*t_init
				z0_rnd -= vz0_rnd*t_init
			
			if checkSkimmer:
				xatskimmer = x0_rnd + (vx0_rnd/vz0_rnd)*(skimmerDist-z0_rnd)
				yatskimmer = y0_rnd + (vy0_rnd/vz0_rnd)*(skimmerDist-z0_rnd)
				ratskimmer = sqrt(xatskimmer**2 + yatskimmer**2)
				ts = np.where(ratskimmer<=skimmerRadius)[0]
			else:
				ts = slice(0, x0_rnd.shape[0])

			
			initialPositions = np.vstack((initialPositions, np.array([x0_rnd[ts], y0_rnd[ts], z0_rnd[ts]]).T))
			initialVelocities = np.vstack((initialVelocities, np.array([vx0_rnd[ts], vy0_rnd[ts], vz0_rnd[ts]]).T))
			
			nGenerated += nParticlesToSim
			nGeneratedGood  = initialPositions.shape[0]
		
		self.initialPositions = np.array(initialPositions)
		self.initialVelocities = np.array(initialVelocities)
		
		skimmerloss_no = 100.*nGeneratedGood/nGenerated
		logging.info('particles coming out of the skimmer (in percent): %.2f\n' % skimmerloss_no)
	
	def addSavedParticles(self, folder, NParticlesOverride = None):
		A = np.genfromtxt(folder + 'init_cond.txt', dtype=np.float)
		if NParticlesOverride is not None:
			self.initialPositions = A[:NParticlesOverride, :3]
			self.initialVelocities=  A[:NParticlesOverride, 3:]/1000.
		else:
			self.initialPositions = A[:, :3]
			self.initialVelocities=  A[:, 3:]/1000.
	
	def calculateCoilSwitching(self, phaseAngleOverride = None):
		""" Generate the switching sequence for the phase angle specified in
		the config file. If phaseAngleOverride is specified, generate for this
		phase angle and ignore config file.

		If the config file gives None as the phase angle, the list of ontimes
		and durations from the config file is used directly without any further
		calculation.
		"""
		if phaseAngleOverride is not None:
			self.propagationProps['phase'] = phaseAngleOverride
		
		# Send the initial position and velocity of the synchronous particle to
		# the C code.
		bunchpos = np.array(self.bunchProps['x0'])
		bunchspeed = np.array(self.bunchProps['v0'])
		self.prop.setSynchronousParticle(self.particleProps['mass'], bunchpos.ctypes.data_as(c_double_p), bunchspeed.ctypes.data_as(c_double_p))
		
		# Send the coil position and properties to the C code.
		coilpos = self.coilProps['position']
		self.prop.setCoils(coilpos.ctypes.data_as(c_double_p), self.coilProps['radius'], self.detectionProps['position'], self.coilProps['NCoils'])
		
		# Send the coil current pulse timing parameters to the C code.
		self.prop.setTimingParameters(self.coilProps['H1'], self.coilProps['H2'], self.coilProps['ramp1'], self.coilProps['timeoverlap'], self.coilProps['rampcoil'], self.coilProps['maxPulseLength'])
		
		## B field along z axis
		# from FEMM or Comsol file
		 
		# Load the analytic solution of on-axis magnetic fields from the file.
		bfieldz = np.require(np.genfromtxt(self.localdir + 'sim_files/bonzaxis.txt', delimiter='\t'), requirements=['C', 'A', 'O', 'W'])
		# bfieldz = np.genfromtxt('sim_files/baxis_Zurich.txt', delimiter='\t') # Zurich Comsol calculation
		
		if self.propagationProps['phase'] == None:
			# if the phase is specified as None in the config file, read in and
			# use the values specified in ontimes and durations without further
			# calculations.
			logging.info('Coil on times and durations will be read from configuration')
			self.ontimes = self.propagationProps['ontimes']
			self.offtimes = self.propagationProps['ontimes'] + self.propagationProps['durations']
			self.prop.overwriteCoils(self.ontimes.ctypes.data_as(c_double_p), self.offtimes.ctypes.data_as(c_double_p))
		else:
			# Otherwise, determine the switching sequence for the specified
			# phase angle. Send parameters to the C code, and call its
			# calculateCoilSwitching function. The new switching times are
			# stored in this class.
			logging.info('Calculating switching sequence for a fixed phase angle of %.2f' % self.propagationProps['phase'])
			currents = self.coilProps['current']
			self.ontimes = np.zeros(self.coilProps['NCoils'], dtype=np.double)
			self.offtimes = np.zeros(self.coilProps['NCoils'], dtype=np.double)	
			
			if not self.prop.calculateCoilSwitching(self.propagationProps['phase'], self.propagationProps['timestepPulse'], bfieldz.ctypes.data_as(c_double_p), self.ontimes.ctypes.data_as(c_double_p), self.offtimes.ctypes.data_as(c_double_p), currents.ctypes.data_as(c_double_p)) == 0:
				raise RuntimeError("Error while calculating coil switching times")
	
	def resetParticles(self, initialZeemanState):
		""" Generate final results arrays by copying starting arrays.
		"""
		self.finalPositions[initialZeemanState] = np.require(self.initialPositions.copy(), requirements=['C', 'A', 'O', 'W'])
		self.finalVelocities[initialZeemanState] = np.require(self.initialVelocities.copy(), requirements=['C', 'A', 'O', 'W'])
		
		self.nParticles = self.initialPositions.shape[0]
		
		self.finalTimes[initialZeemanState] = np.require(np.empty((self.nParticles, )))

		return 0
	
	def loadBFields(self):
		""" Load analytical magnetic fields from text files stored in the
		sim_files directory. The loaded arrays are passed to the simulation
		object by calling setBFields.
		"""
		## B field coil
		Bz_n = np.genfromtxt(self.localdir + 'sim_files/Bz_n.txt', delimiter='\t').T # contains Bz field as a grid with P(r,z) (from analytic solution)
		Br_n = np.genfromtxt(self.localdir + 'sim_files/Br_n.txt', delimiter='\t').T # contains Br field as a grid with P(r,z) (from analytic solution)
		
		self.raxis = np.genfromtxt(self.localdir + 'sim_files/raxis.txt', delimiter='\t') # raxis as one column
		self.zaxis = np.genfromtxt(self.localdir + 'sim_files/zaxis.txt', delimiter='\t') # zaxis as one row
		
		zdist = self.zaxis[1] - self.zaxis[0] # spacing B field z axis (in mm)
		rdist = self.raxis[1] - self.raxis[0] # spacing B field r axis (in mm)
		bzextend = -self.zaxis[0] # dimension B field along decelerator z axis (in mm)
		sizB = Bz_n.shape[1]
		
		self.Bz_n_flat = Bz_n.flatten()
		self.Br_n_flat = Br_n.flatten()
		
		sizZ = self.zaxis.shape[0]
		sizR = self.raxis.shape[0]
		
		self.prop.setBFields(self.Bz_n_flat.ctypes.data_as(c_double_p), self.Br_n_flat.ctypes.data_as(c_double_p), self.zaxis.ctypes.data_as(c_double_p), self.raxis.ctypes.data_as(c_double_p), bzextend, zdist, rdist, sizZ, sizR, sizB)
	
	def preparePropagation(self, overwrite_currents=None):
		""" Prepare to propagate the simulation by setting parameters from
		class variables. Parameters are set in C functions throughsetSkimmer,
		setCoils, and setPropagationParameters. Optional argument
		overwrite_currents replaces the currents loaded from config.info file.
		"""
		sradius = self.skimmerProps['radius']
		sbradius = self.skimmerProps['backradius']
		slength = self.skimmerProps['length']
		spos = self.skimmerProps['position']
		alpha = np.arctan((sbradius - sradius)/slength)
		self.prop.setSkimmer(spos, slength, sradius, alpha)
		
		self.coilpos = self.coilProps['position']
		cradius = self.coilProps['radius']
		nCoils = int(self.coilProps['NCoils'])
		self.prop.setCoils(self.coilpos.ctypes.data_as(c_double_p), cradius, self.detectionProps['position'], nCoils)
		
		tStart = self.propagationProps['starttime']
		tStop = self.propagationProps['stoptime']
		dT =  self.propagationProps['timestep']
		
		self.prop.setPropagationParameters(tStart, dT, 1, (tStop - tStart)/dT)
		self.current_buffer = np.zeros(((tStop - tStart)/dT, nCoils), dtype=np.double)

		if overwrite_currents is None:
			self.currents = self.coilProps['current']
		else:
			self.currents = np.array(overwrite_currents)
		if not self.prop.precalculateCurrents(self.current_buffer.ctypes.data_as(c_double_p), self.currents.ctypes.data_as(c_double_p)) == 0:
			raise RuntimeError("Error precalculating currents!")
		
	def propagate(self, zeemanState = -1):
		""" Propagate a cloud of particles in a given Zeeman state. A
		zeemanState of -1 corresponds to decelerator off. Other Zeeman states
		are enumerated in order of increasing energy, from low-field seeking to
		high-field seeking. Initial particle positions and velocities are
		copied to the final arrays as the C function overwrites these.
		"""
		self.resetParticles(zeemanState)				
		pos = self.finalPositions[zeemanState]
		vel = self.finalVelocities[zeemanState]
		times = self.finalTimes[zeemanState]
		self.prop.doPropagate(pos.ctypes.data_as(c_double_p), vel.ctypes.data_as(c_double_p),  times.ctypes.data_as(c_double_p), self.nParticles, zeemanState)
		return pos, vel, times
	
	def getTimeDependence(self, nSteps, zeemanState = 0):
		self.preparePropagation()
		self.resetParticles(zeemanState)
		tStart = self.propagationProps['starttime']
		tStop = self.propagationProps['stoptime']
		dT =  self.propagationProps['timestep']
		maxSteps = (tStop - tStart)/dT

		steps = np.linspace(1, maxSteps, nSteps).astype(int)
		steps = np.insert(steps, 0, steps[0] - 1)
		positions = []
		velocities = []
		for i in np.arange(nSteps):
			self.prop.setPropagationParameters(steps[i] + tStart, dT, steps[i] + 1, steps[i+1] - steps[i])
			pos = self.finalPositions[zeemanState]
			vel = self.finalVelocities[zeemanState]
			ftimes = self.finalTimes[zeemanState]
			self.prop.doPropagate(pos.ctypes.data_as(c_double_p), vel.ctypes.data_as(c_double_p),  ftimes.ctypes.data_as(c_double_p), self.nParticles, zeemanState)
			positions.append(np.copy(pos[:, :]))
			velocities.append(np.copy(vel[:, :]))
		return np.array(positions), np.array(velocities)


if __name__ == '__main__':
	import ConfigChecker
	import argparse

	parser = argparse.ArgumentParser()
	parser.add_argument('wd', 
			help='The working directory containing the config.info file.')
	parser.add_argument('-c', 
			help='Console mode. Will not produce plots on completion',
			action='store_true')
	parser.add_argument('-q', 
			help='Quiet mode. Does not produce any output; still log messages to file.', 
			action='store_true')
	args = parser.parse_args()
	folder = args.wd

	# Set up logging to console and file.
	# logging.basicConfig(format='%(levelname)s : %(message)s',
	# level=logging.DEBUG)
	logging.basicConfig(
			format='%(asctime)s - %(levelname)-8s : %(message)s',
			datefmt='%d%m%y %H:%M',
			filename=os.path.join(folder, 'log.txt'),
			filemode='w',
			level=logging.DEBUG)
	if not args.q:
		ch = logging.StreamHandler()
		ch.setLevel(logging.DEBUG)
		ch.setFormatter(logging.Formatter('%(levelname)-8s - %(message)s'))
		logging.getLogger().addHandler(ch)

	config_file = os.path.join(folder, 'config.info')
	logging.info('Running analysis in folder %s' % folder)
	if not os.path.exists(config_file):
		logging.critical('Config file not found at %s' % config_file)
		sys.exit(1)

	flyer = ZeemanFlyer()
	# Load parameters from config file and test that all is present and
	# correct. Exit if there is a problem.
	flyer.loadParameters(config_file)
	try:
		ConfigChecker.test_parameters(flyer)
	except RuntimeError as e:
		logging.critical(e)
		sys.exit(1)

	# Initialise the flyer calculation.  Generate the cloud of starting
	# positions and velocities
	flyer.addParticles(checkSkimmer=True)
	# Generate the switching sequence for the selected phase angle.
	flyer.calculateCoilSwitching()
	# Load pre-calculated magnetic field mesh.
	flyer.loadBFields()
	# Transfer data to propagation library.
	flyer.preparePropagation()

	np.save(os.path.join(folder + 'initialpos.npy'), flyer.initialPositions)
	np.save(os.path.join(folder, 'initialvel.npy'), flyer.initialVelocities)
	
	totalGood1 = 0
	allvel1 = []
	alltimes1 = []
	target_vel = flyer.optimiserProps['targetSpeed']
	# loop over each Zeeman state in sequence from low-field seeking to
	# high-field seeking. First iteration is -1, which corresponds to
	# decelerator off.
	for z in np.arange(-1, flyer.bunchProps['zeemanStates']):
		logging.info('running for zeeman state %d' % z)
		pos, vel, times = flyer.propagate(z)
		ind = np.where((pos[:, 2] > flyer.detectionProps['position'])) # all particles that reach the end
		# if z in [0, 1]:
		# 	plt.figure(0)
		# 	plt.hist(vel[ind, 2].flatten(), bins = np.arange(0, 1, 0.005), histtype='step', color='r')
		# 	plt.figure(1)
		# 	plt.hist(times[ind], bins=np.linspace(200, 1200, 101), histtype='step', color='r')
		allvel1.extend(vel[ind, 2].flat)
		alltimes1.extend(times[ind])
		indg1 = np.where((pos[:, 2] > flyer.detectionProps['position']) & (vel[:, 2] < 1.1*target_vel) & (vel[:, 2] > 0.9*target_vel))[0]
		logging.info('%d particles detected within 10%% of target velocity' % indg1.shape[0])
		totalGood1 += indg1.shape[0]

		# Save each Zeeman state in a separate file.
		np.save(os.path.join(folder, 'finalpos' + str(z) + '.npy'), pos)
		np.save(os.path.join(folder, 'finalvel' + str(z) + '.npy'), vel)
		np.save(os.path.join(folder, 'finaltimes' + str(z) + '.npy'), times)

	np.save(os.path.join(folder, 'initialpos.npy'), flyer.initialPositions)
	np.save(os.path.join(folder, 'initialvel.npy'), flyer.initialVelocities)


	if not (args.q or args.c):
		plt.figure()
		plt.hist(allvel1, bins = np.arange(0, 1, 0.005), histtype='step', color='r')
		plt.figure()
		plt.hist(alltimes1, bins=np.linspace(200, 1200, 101), histtype='step', color='r')
		plt.show()
