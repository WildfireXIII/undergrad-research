import tensorflow as tf
import gym
import numpy as np

from skimage.transform import resize
from skimage.color import rgb2grey
from skimage.io import imsave

import random
import time

import subprocess
import multiprocessing
import threading
#from Queue import Queue

import scipy
import scipy.signal


# https://jaromiru.com/2017/02/16/lets-make-an-a3c-theory/ 
# https://jaromiru.com/2017/03/26/lets-make-an-a3c-implementation/ 
# https://github.com/jaara/AI-blog/blob/master/CartPole-A3C.py
# https://medium.com/emergent-future/simple-reinforcement-learning-with-tensorflow-part-8-asynchronous-actor-critic-agents-a3c-c88f72a5e9f2 
# https://medium.com/@henrymao/reinforcement-learning-using-asynchronous-advantage-actor-critic-704147f91686 


# returns a set of operations to set all weights of destination scope to values of weights from source scope
def getWeightChangeOps(scopeSrc, scopeDest):
    srcVars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scopeSrc)
    destVars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scopeDest)

    assignOps = []
    for srcVar, destVar in zip(srcVars, destVars):
        assignOps.append(tf.assign(srcVar, destVar))

    return assignOps

# calculates discounted return TODO: figure out why this actually works?
def discount(x, gamma):
    return scipy.signal.lfilter([1], [1, -gamma], x[::-1], axis=0)[::-1]


# globals NOTE: caution!
atariEnvFree = True
T = 0


# hyperparameters
GAME = "SpaceInvaders-v0"
ACTION_SIZE = 6

ACTION_REPEAT = 4
STATE_FRAME_COUNT = 4

LEARNING_RATE = .0001
NUM_WORKERS = 16
#NUM_WORKERS = 1


t_MAX = 5
T_MAX = 40000 # (epoch training steps)
#T_MAX = 1000 # (epoch training steps)

GAMMA = .99
BETA = .01
ALPHA = .99 # rmsprop decay

TEST_RUN_COUNT = 5

EPOCHS = 100


class Manager:

    def __init__(self):

        self.optimizer = tf.train.RMSPropOptimizer(LEARNING_RATE, ALPHA, use_locking=True)
        self.globalNetwork = Network('global', self.optimizer)
        self.globalNetwork.buildGraph()
        merged_summaries = tf.summary.merge_all()
        with tf.Session() as sess:
            self.train_writer = tf.summary.FileWriter('../tensorboard_data/a3c_' + GAME, sess.graph)
        
        
    def buildWorkers(self):
        print("Number of threads: ", NUM_WORKERS)
        self.workers = []
        for i in range(NUM_WORKERS):
            self.workers.append(Worker("worker_" + str(i), self.optimizer))


    def runEpoch(self, epochNum):
        with tf.Session() as sess:
            coordinator = tf.train.Coordinator()
            sess.run(tf.global_variables_initializer())
        
            # logging things

            # create worker threads
            worker_threads = []
            for worker in self.workers:
                worker_function = lambda: worker.work(sess, coordinator, self.train_writer)
                t = threading.Thread(target=worker_function)
                t.start()
                worker_threads.append(t)
                
            self.train_writer.add_graph(sess.graph)

            coordinator.join(worker_threads)

    def singleTestRun(self, epochNum, render=False):
        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
            e = Environment()
            if render: e.env = gym.wrappers.Monitor(e.env, './runs/epoch_' + str(epochNum), force=True)
            state = e.getInitialState()
            terminal = False
            while not terminal:
                policyVec = sess.run(self.globalNetwork.policy_out, feed_dict={self.globalNetwork.input: [state]})
                action = np.argmax(policyVec)
                state, reward, terminal = e.act(action)
                    
        print("FINAL SCORE:",e.finalScore)
        return e.finalScore

                    
        
    def testGlobal(self, epochNum):
        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())

            scores = []

            for i in range(TEST_RUN_COUNT):
                #score = self.singleTestRun()
                if i == 0: score = self.singleTestRun(epochNum, True)
                else: score = self.singleTestRun(epochNum)
                scores.append(score)
                
            #avgScore = np.average(scores)
            
            score_log = sess.run([self.globalNetwork.log_score], feed_dict={self.globalNetwork.score: scores})
            self.train_writer.add_summary(score_log[0], epochNum)

    def run(self):
        global T
        #with tf.Session() as sess:
            #coordinator = tf.train.Coordinator()
            #sess.run(tf.global_variables_initializer())
        
            # logging things
            #merged_summaries = tf.summary.merge_all()
            #train_writer = tf.summary.FileWriter('../tensorboard_data/a3c_full' , sess.graph)

            # create worker threads
            #worker_threads = []
            #for worker in self.workers:
                #worker_function = lambda: worker.work(sess, coordinator, train_writer)
                #t = threading.Thread(target=worker_function)
                #t.start()
                #worker_threads.append(t)
                
            #train_writer.add_graph(sess.graph)

            #coordinator.join(worker_threads)

        self.buildWorkers()
        for i in range(EPOCHS):
            T = 0
            self.runEpoch(i)
            self.testGlobal(i)
            #subprocess.call(['notify-send', "A3C training completed!"])
            subprocess.call(['notify-send', "Epoch " + str(i) + " complete"])


            #exit = False
            #user = input("type exit to quit, or anything else to run: ")
            #if user == "exit": exit = True
            
            #while not exit:
                ## test it!
                #e = Environment()
                #e.env = gym.wrappers.Monitor(e.env, './tmp/testing', force=True)
                #state = e.getInitialState()
                #terminal = False
                #while not terminal:
                    #policyVec = sess.run(self.globalNetwork.policy_out, feed_dict={self.globalNetwork.input: [state]})
                    #action = np.argmax(policyVec)
#
                    #e.env.render()
                    #state, reward, terminal = e.act(action)
                #
                #user = input("type exit to quit, or anything else to run: ")
                #if user == "exit": exit = True
 
            

class Worker:

    def __init__(self, name, optimizer):
        self.name = name
        #self.optimizer = optimizer
        
        self.network = Network(self.name, optimizer)
        self.network.buildGraph()

        self.resetWeights = getWeightChangeOps("global", self.name)

        print("Worker",self.name,"initialized...")

    def train(self, history, session, bootstrap):
        history = np.array(history)
        states = history[:,0]
        actions = history[:,1]
        rewards = history[:,2]
        states_next = history[:,3]
        values = history[:,4]


        values = np.asarray(values.tolist() + [bootstrap]) # TODO: figure out what the bootstrapping stuff is?
        #rewards = np.asarray(rewards.tolist() + [bootstrap]) # TODO: figure out what the bootstrapping stuff is?
        #print("rewards:",rewards.shape)
        discountedRewards = discount(rewards, GAMMA)
        #print(discountedRewards)
        #print("rewards:",rewards.shape)
        #print("values:",values[1:].shape)

        # NOTE: values[1:] = the next state, values[:-1] = the previous state
        # A = Q - V(s)
        # Q = r + yV(s')
        # A = r + yV(s') - V(S)
        #print("values:",values[1:].shape)
        #print("values:",values[:-1].shape)
        advantages = rewards + GAMMA*values[1:] - values[:-1]
        #print("advnatages:",advantages.shape)

        # TODO: supposedly we have to discount advantages, I don't know if that is correct or not (shouldn't we just use discounted rewards?)
        advantages = discount(advantages, GAMMA)

        #print(history.shape)
        #print(states.shape)
        states = np.asarray(states)
        states = np.stack(states, 0)
        #states = np.dstack(states)
        #states = np.array(np.split(states, 3))
        #states = np.split(states, 1)
        #print(states.shape)


        # apply gradients to global network
        summary, p_loss, v_loss, _ = session.run([self.network.log_op, self.network.policy_loss, self.network.value_loss, self.network.apply_gradients], feed_dict={self.network.input: states, self.network.actions: actions, self.network.target_v: discountedRewards, self.network.advantages: advantages})
        #p_loss, v_loss, _ = session.run([self.network.policy_loss, self.network.value_loss, self.network.apply_gradients], feed_dict={self.network.input: states, self.network.actions: actions, self.network.target_v: discountedRewards, self.network.advantages: advantages})
        #p_loss, v_loss = session.run([self.network.policy_loss, self.network.value_loss], feed_dict={self.network.input: states, self.network.actions: actions, self.network.target_v: discountedRewards, self.network.advantages: advantages})

        #print("Policy loss:",p_loss,"Value loss:",v_loss)
        return summary, p_loss, v_loss
        #return p_loss, v_loss


        

        
        
        
    
    def work(self, session, coordinator, train_writer):
        t = 0
        #T = 0
        global T
        while not coordinator.should_stop():

            # reset ops
            session.run(self.resetWeights)

            # get an environment instance
            #time.sleep(random.uniform(0.0,0.5))
            self.env = Environment()

            history = []

            t_start = t

            # get state s_t
            s_t = self.env.getInitialState()
            terminal = False

            # repeat until terminal state
            while not terminal:
                # perform a_t according to policY9a_t|s_t; theta_)
                policyVec, v = session.run([self.network.policy_out, self.network.value_out], feed_dict={self.network.input: [s_t]})
                a_t = np.argmax(policyVec)

                #if self.name == "worker_0":
                    #self.env.env.render()

                # receive reward r_t and new state s_{t+1}
                #a_t = a.act(s_t)
                s_t1, r_t, terminal = self.env.act(a_t)

                history.append([s_t, a_t, r_t, s_t1, v[0,0]])

                s_t = s_t1

                t += 1
                T += 1

                if t - t_start >= t_MAX:
                    summary, p_loss, v_loss = self.train(history, session, v[0,0])
                    train_writer.add_summary(summary, t)
                    #p_loss, v_loss = self.train(history, session, v[0,0], merged_summaries)
                    print(self.name,"[" + str(T) + "]","- Policy loss:",p_loss,"Value loss:",v_loss)
                    history = []
                    t_start = t
                    session.run(self.resetWeights)
                    
                    
            
            if len(history) > 0:
                summary, p_loss, v_loss = self.train(history, session, 0.0)
                #p_loss, v_loss = self.train(history, session, 0.0, merged_summaries)
                print("Policy loss:",p_loss,"Value loss:",v_loss)

            #if T == 500: break
            #if T == 100: break
            if T > T_MAX: break

            #R = 0
            #if not terminal: R = 


            #for i in range(0, t):
                #transition = history[i - t_start]
                #s_i = transition[0]
                #a_i = transition[1]
                #r_i = transition[2]

                #R = r_i + self.GAMMA*R

            
                
                
                
            
            
    

    
    


class Network:
    def __init__(self, scope, optimizer):
        self.scope = scope
        self.optimizer = optimizer
        
        

    def buildGraph(self):
        print("Building graph with scope", self.scope)
        with tf.variable_scope(self.scope):
            #self.input = tf.placeholder(tf.float32, shape=(1,84,84,4), name='input') # TODO: pretty sure that shape isn't right
            self.input = tf.placeholder(tf.float32, shape=(None,84,84,4), name='input') # TODO: pretty sure that shape isn't right
            
            # 16 filters, kernel size of 8, stride of 4
            with tf.name_scope('conv1'):
                self.w1 = tf.Variable(tf.random_normal([8, 8, 4, 16]), name='weights1')
                self.b1 = tf.Variable(tf.random_normal([16]), name='bias1')
                self.conv1 = tf.nn.conv2d(self.input, self.w1, [1, 4, 4, 1], "VALID", name='conv1') 
                self.conv1_relu = tf.nn.relu(tf.nn.bias_add(self.conv1, self.b1))
                
            # 32 filters, kernel size of 4, stride of 2
            with tf.name_scope('conv2'):
                self.w2 = tf.Variable(tf.random_normal([4, 4, 16, 32]), name='weights2')
                self.b2 = tf.Variable(tf.random_normal([32]), name='bias2')
                self.conv2 = tf.nn.conv2d(self.conv1_relu, self.w2, [1, 2, 2, 1], "VALID", name='conv2') 
                self.conv2_relu = tf.nn.relu(tf.nn.bias_add(self.conv2, self.b2))

                # flattened size is 9*9*32 = 2592
                self.conv2_out = tf.reshape(self.conv2_relu, [-1, 2592], name='conv2_flatten') 
                

            # fully connected layer with 256 hidden units
            with tf.name_scope('fully_connected'):
                self.fc_w = tf.Variable(tf.random_normal([2592, 256]), name='fc_weights') 
                self.fc_b = tf.Variable(tf.random_normal([256]), name='fc_biases') # fully connected biases

                self.fc_out = tf.nn.relu_layer(self.conv2_out, self.fc_w, self.fc_b, name='fc_out')

            # policy output, policy = distribution of probabilities over actions, use softmax to choose highest probability action
            with tf.name_scope('policy'):
                self.policy_w = tf.Variable(tf.random_normal([256, ACTION_SIZE]), name='policy_w')
                
                # TODO: do we need biases as well?
                self.policy_out = tf.nn.softmax(tf.matmul(self.fc_out, self.policy_w))
                
                ## NOTE: used for gradient calculations
                #self.policy_log_prob = tf.log(self.policy_out)

            # Only a SINGLE output, just a single linear value
            with tf.name_scope('value'):
                self.value_w = tf.Variable(tf.random_normal([256, 1]), name='value_w')

                # TODO: do we need a bias for this? (edit: I'm pretty sure since it's a single linear value, there's no point in having a bias value?)

                self.value_out = tf.matmul(self.fc_out, self.value_w)



            # policy gradient calculation
            #self.R = tf.placeholder(tf.float32, shape=(1), name='reward_input')

            #self.entropy = tf.reduce_sum(self.policy_out * self.policy_log_prob, name='entropy')
            
            #with tf.name_scope('advantage'):
                #self.A = self.R - self.value_out

            #with tf.name_scope("policy_loss"):
                #self.policy_loss = self.policy_log_prob*self.A # NOTE: the graph of this doesn't look right...the mul term doesn't go into the gradient at all, is that correct?
                #self.dtheta_ = tf.gradients(self.policy_gradient_term, [self.policy_w, self.fc_w, self.w2, self.w1]) # TODO: no idea if this is correct at all

            # TODO: add an entropy term to the gradient


            #with tf.name_scope('value_loss'):
                #self.value_loss = tf.square(self.A)
                #self.dtheta_v_ = tf.gradients(self.A, [self.value_w]) # TODO: does this still apply to all weights or only value weights?
                

            #with tf.name_scope("objective"):
                #self.full_objective = self.policy_loss + self.value_loss_weight*self.value_loss + self.entropy*self.BETA
                #localvars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES) # TODO: eventually need a scope variable passed in as well I think? (once go to multithreading)
                #self.gradients = tf.gradients(self.full_objective, localvars)

                # TODO: still doesn't look right, the full_objective stuff doesn't actually lead into the gradients variable??

            if self.scope != 'global':
                self.actions = tf.placeholder(shape=[None], dtype=tf.int32, name='actions')
                self.target_v = tf.placeholder(shape=[None], dtype=tf.float32, name='target_v',)
                self.advantages = tf.placeholder(shape=[None], dtype=tf.float32, name='advantages')
                
                self.actions_onehot = tf.one_hot(self.actions, ACTION_SIZE, dtype=tf.float32)
                self.responsible_outputs = tf.reduce_sum(self.policy_out * self.actions_onehot, [1])
                
                # losses
                # NOTE: .5's seem arbitrary, these should be set as hyperparameters
                self.value_loss = .5 * tf.reduce_sum(tf.square(self.target_v - tf.reshape(self.value_out, [-1])))
                self.entropy = -tf.reduce_sum(self.policy_out * self.actions_onehot, [1])
                self.policy_loss = -tf.reduce_sum(tf.log(self.responsible_outputs)*self.advantages)
                self.loss = .5 * self.value_loss + self.policy_loss - self.entropy * BETA # NOTE: .01 should also be a hyperparameter


                # summaries
                self.log_value_loss = tf.summary.scalar('value_loss', self.value_loss)
                self.log_policy_loss = tf.summary.scalar('policy_loss', self.policy_loss)
                self.log_loss = tf.summary.scalar('loss', tf.reduce_sum(self.loss))
                #print(self.loss)


                self.log_op = tf.summary.merge([self.log_value_loss, self.log_policy_loss, self.log_loss])
                #self.log_op = tf.summary.merge([self.log_value_loss, self.log_policy_loss])

                local_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, self.scope)
                self.gradients = tf.gradients(self.loss, local_vars)
                self.var_norms = tf.global_norm(local_vars)
                self.clipped_gradients, self.gradient_norms = tf.clip_by_global_norm(self.gradients, 40.0) # TODO: where is 40 coming from???
                
                global_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'global')
                self.apply_gradients = self.optimizer.apply_gradients(zip(self.clipped_gradients, global_vars))
                
                
                


            if self.scope == 'global':
                self.score = tf.placeholder(shape=[None], dtype=tf.float32, name='score')
                self.log_score_avg = tf.summary.scalar('score_avg', tf.reduce_mean(self.score))
                self.log_score_min = tf.summary.scalar('score_min', tf.reduce_min(self.score))
                self.log_score_max = tf.summary.scalar('score_max', tf.reduce_max(self.score))
                self.log_score = tf.summary.merge([self.log_score_avg, self.log_score_min, self.log_score_max])
                #self.merged_summaries = tf.summary.merge_all()
                #self.sess.run(tf.global_variables_initializer())
                
               # self.train_writer = tf.summary.FileWriter('../tensorboard_data/a3c_full' , self.sess.graph)
               # self.train_writer.add_graph(self.sess.graph)




        




class Environment:
    def __init__(self):
        global atariEnvFree
        print("Initializing environment...")

        while not atariEnvFree: time.sleep(.01) # NOTE: some weird thing the atari emulator needs to make sure two threads don't simultaneously create an environment
        atariEnvFree = False
        self.env = gym.make("SpaceInvaders-v0")
        #self.env = gym.make("Breakout-v0")
        atariEnvFree = True
        
        self.seqSize = STATE_FRAME_COUNT
        self.rawFrameSeq = []
        self.frameSeq = []

        self.finalScore = 0

        print("Environment initialized")

    def getInitialState(self):
        print("Getting an initial state...")
        frame = self.preprocessFrame(self.env.reset())
        self.frameSeq.append(frame) # TODO: make this based off of self.seqsize
        self.frameSeq.append(frame)
        self.frameSeq.append(frame)
        self.frameSeq.append(frame)
        
        self.rawFrameSeq.append(frame) # TODO: make this based off of self.seqsize
        self.rawFrameSeq.append(frame)
        self.rawFrameSeq.append(frame)
        self.rawFrameSeq.append(frame)

        state = np.dstack(self.frameSeq)
        
        return state

    def preprocessFrame(self, frame):
        frame = resize(frame, (110,84))
        frame = frame[18:102,0:84]
        frame = rgb2grey(frame)
        return frame


    def act(self, action):

        cumulativeReward = 0.0
        for i in range(ACTION_REPEAT):
            observation, reward, terminal, info = self.env.step(action)
            cumulativeReward += reward
            observationFrame = self.preprocessFrame(observation)
            
            self.rawFrameSeq.pop(0)
            self.rawFrameSeq.append(observationFrame)

            self.frameSeq.pop(0)
            cleanedFrame = np.maximum(self.rawFrameSeq[-1], self.rawFrameSeq[-2])
            #imsave('test.png', cleanedFrame)
            self.frameSeq.append(cleanedFrame)
            
            if terminal: 
                print("TERMINAL STATE REACHED")
                break
            
        state = np.dstack(self.frameSeq)
        
        self.finalScore += cumulativeReward
        
        return state, cumulativeReward, terminal




m = Manager()
m.run()
