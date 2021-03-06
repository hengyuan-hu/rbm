import keras.backend as K

import tensorflow as tf
import numpy as np
import os
import utils
import math
import time


class DEMTrainer(object):
    def __init__(self, sess, dataset, dem, vis_fn, output_dir):
        self.dataset = dataset
        self.dem = dem
        self.vis_fn = vis_fn
        self.sess = sess
        self.log = []
        self.output_dir = output_dir
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

    @property
    def x_shape(self):
        return [None] + list(self.dataset.x_shape)

    @property
    def z_shape(self):
        return [None, self.dem.num_z]

    @property
    def train_xs(self):
        return self.dataset.train_xs

    @property
    def test_xs(self):
        return self.dataset.test_xs

    def _test_init(self):
        test_input = self.test_xs[:25]
        z = self.dem.encoder(test_input)
        z_recon, _ = self.dem.vhv(z)
        # print 'z recon shape:', z_recon.get_shape().as_list()
        # x_recon = self.dem.decoder(z_recon)
        # x_recon = self.sess.run(x_recon, {K.learning_phase(): 0})
        test_z_recon = self.sess.run(z_recon, {K.learning_phase(): 0})
        x_recon = self.dem.decoder.predict(test_z_recon)
        output_path =  os.path.join(self.output_dir, 'test_init.png')
        self._save_samples(x_recon, output_path)

    def test_decode(self):
        test_input = self.test_xs[:25]
        z = self.dem.encoder.predict(test_input)
        x_recon = self.dem.decoder(z)
        # feed z into decoder input since K.shape need this to figure out batchsize
        x = self.sess.run(x_recon, {K.learning_phase(): 0,
                                    self.dem.decoder.input: z})
        print 'it passes!'
        output_path =  os.path.join(self.output_dir, 'test_decode.png')
        self._save_samples(x, output_path)

    def train(self, train_config, sampler, sampler_generator):
        # building graphs
        # encoder_x = tf.placeholder(tf.float32, self.x_shape)
        # encoder_target_z = tf.placeholder(tf.float32, self.z_shape)
        # encoder_cost = self.dem.encoder_cost(encoder_x, encoder_target_z)

        ae_x = tf.placeholder(tf.float32, self.x_shape)
        encoder_fe_cost = self.dem.free_energy_wrt_x(ae_x)
        ae_cost = self.dem.autoencoder_cost(ae_x)
        ae_vars = self.dem.get_trainable_vars(['encoder', 'decoder'])

        fe_cost_factor = 5e-5

        encoder_final_conv = self.dem.get_trainable_vars(['encoder'])[-2]
        efc_grad_mean = tf.reduce_mean(
            tf.abs(tf.gradients(fe_cost_factor * encoder_fe_cost, encoder_final_conv)[0]))
        aec_grad_mean = tf.reduce_mean(
            tf.abs(tf.gradients(ae_cost, encoder_final_conv)[0]))

        rbm_z_data = tf.placeholder(tf.float32, self.z_shape)
        rbm_z_model = tf.placeholder(tf.float32, self.z_shape)
        rbm_loss, rbm_cost = self.dem.rbm_loss_and_cost(rbm_z_data, rbm_z_model)

        opt_rbm = tf.train.GradientDescentOptimizer(
            train_config.lr).minimize(rbm_cost)
        opt_ae = tf.train.GradientDescentOptimizer(
            0.01).minimize(ae_cost + fe_cost_factor * encoder_fe_cost, var_list=ae_vars)

        if sampler.is_persistent:
            print '>>>>>>>> using pcd-%d' % train_config.cd_k
            sample_op, sampler_updates = sampler.sample()
        else:
            print '>>>>>>>> using cd-%d' % train_config.cd_k
            sample_op, sampler_updates = sampler.sample(rbm_z_data)

        # finish building all graphs, init only new variables
        utils.initialize_uninitialized_variables_by_keras()
        # self._test_init()

        num_batches = int(math.ceil(
            len(self.train_xs) / float(train_config.batch_size)))

        for e in range(train_config.num_epoch):
            t = time.time()
            np.random.shuffle(self.train_xs)
            loss_vals = {'decoder': np.zeros(num_batches),
                         'rbm': np.zeros(num_batches),
                         'encoder': np.zeros(num_batches)}

            for b in range(num_batches):
                x_data = self.train_xs[b * train_config.batch_size
                                       :(b+1) * train_config.batch_size]
                # upward pass
                z_data = self.dem.encoder.predict(x_data)
                # run sampler, get z_model
                feed_dict = {} if sampler.is_persistent else {rbm_z_data: z_data}
                z_model, _ = self.sess.run([sample_op, sampler_updates], feed_dict)

                print 'z_data, mean: %s, std:%s' % (z_data.mean(), z_data.std())
                print 'z_model, mean: %s, std:%s' % (z_model.mean(), z_model.std())

                # # downward pass
                # x_model = self.dem.decoder.predict(z_model)

                # check gradient magnitute
                # grad_fe, grad_ae = self.sess.run(
                #     [efc_grad_mean, aec_grad_mean],
                #     {ae_x: x_data,
                #      self.dem.decoder.input: z_data,
                #      K.learning_phase(): 1})
                # print 'grad_fe:', grad_fe, 'grad_ae:', grad_ae

                # update encoder decoder weights
                feed_dict = {ae_x: x_data,
                             self.dem.decoder.input: z_data, # for batch_size
                             K.learning_phase(): 1} # for noise
                loss_vals['decoder'][b],loss_vals['encoder'][b], _ = self.sess.run(
                    [ae_cost, encoder_fe_cost, opt_ae], feed_dict)

                # update rbm weights
                feed_dict = {rbm_z_data: z_data, rbm_z_model: z_model}
                loss_vals['rbm'][b], _ = self.sess.run(
                    [rbm_loss, opt_rbm], feed_dict)

            self.log.append(
                'Epoch %d, RBM Loss: %.4f, Deocder Loss: %.4f, Encoder Loss: %.4f' \
                % (e+1, loss_vals['rbm'].mean(),
                   loss_vals['decoder'].mean(), loss_vals['encoder'].mean()))
            print self.log[-1]
            print '\tTime Taken: %ss' % (time.time() - t)

            if True:
                el1_weights = self.dem.encoder.layers[1].get_weights()
                dl1_weights = self.dem.decoder.layers[-2].get_weights()
                print '\tencoder L1 weights sum: %s, decoder L1 weights sum: %s' \
                    % (el1_weights[0].sum(), dl1_weights[0].sum())

            if (e+1) % 5 == 0:
                samples = self._draw_samples(sampler_generator())
                samples_path = os.path.join(
                    self.output_dir, 'samples-epoch%d.png' % (e+1))
                chain_path = os.path.join(
                    self.output_dir, 'neg-samples-epoch%d.png' % (e+1))
                # print 'saving imgs'
                self._save_samples(samples, samples_path)
            if (e+1) % 100 == 0:
                self.dem.save_model(self.sess, self.output_dir, 'epoch_%d_' % (e+1))
                self.dump_log()
                # self._save_samples(x_model, chain_path)

    def dump_log(self, output_dir=None):
        if output_dir is None:
            output_dir = self.output_dir
        path = os.path.join(output_dir, 'dem_train.log')
        with open(path, 'w') as f:
            f.write('\n'.join(self.log))
            f.write('\n')

    def _draw_samples(self, sampler):
        """Use a new sampler to draw samples from the trained model.
        """
        assert sampler.is_persistent
        # init new variables created by new sampler
        utils.initialize_uninitialized_variables_by_keras()

        sample_op, sampler_updates = sampler.sample()
        for i in range(sampler.burnin):
            self.sess.run([sample_op, sampler_updates])

        samples, _ = self.sess.run([sample_op, sampler_updates])
        print 'in _draw_samples: samples min: %.4f, max: %.4f' \
            % (samples.min(), samples.max())
        return samples

    def _save_samples(self, samples, img_path):
        samples = self.dem.decoder.predict(samples)
        batch_size = len(samples)
        rows, cols = utils.factorize_number(batch_size)
        self.vis_fn(samples, rows, cols, img_path)
