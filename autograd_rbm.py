import numpy as np
import cPickle
import os
import time
import tensorflow as tf
import matplotlib.pyplot as plt


def get_session():
    # config = tf.ConfigProto(log_device_placement=True)
    config = tf.ConfigProto() # log_device_placement=True)
    config.gpu_options.allow_growth = True
    log_device_placement=True
    return tf.Session(config=config)


class RBM(object):
    def __init__(self, num_vis, num_hid, name):
        self.num_vis = num_vis
        self.num_hid = num_hid
        self.name = name

        with tf.variable_scope(name):
            self.weights = tf.get_variable(
                'weights', shape=[self.num_vis, self.num_hid],
                initializer=tf.random_normal_initializer(0, 0.01))
            self.vbias = tf.get_variable(
                'vbias', shape=[1, self.num_vis],
                initializer=tf.constant_initializer(0.0))
            self.hbias = tf.get_variable(
                'hbias', shape=[1, self.num_hid],
                initializer=tf.constant_initializer(0.0))

            # # for testing
            # self.vbias = tf.get_variable(
            #     'vbias', shape=[1, self.num_vis],
            #     initializer=tf.random_normal_initializer(0, 0.01))
            # self.hbias = tf.get_variable(
            #     'hbias', shape=[1, self.num_hid],
            #     initializer=tf.random_normal_initializer(0, 0.01))

        self.params = [self.weights, self.vbias, self.hbias]
        self.sess = get_session()

    def compute_up(self, vis):
        hid_p = tf.nn.sigmoid(tf.matmul(vis, self.weights) + self.hbias)
        return hid_p

    def compute_down(self, hid):
        vis_p = tf.nn.sigmoid(tf.matmul(hid, tf.transpose(self.weights)) + self.vbias)
        return vis_p

    def sample(self, ps):
        rand_uniform = tf.random_uniform(ps.get_shape().as_list(), 0, 1)
        samples = tf.to_float(rand_uniform < ps)
        return samples

    def free_energy(self, vis_samples):
        """Compute the free energy defined on visibles.

        return: free energy of shape: [batch_size, 1]
        """
        vbias_term = tf.matmul(vis_samples, self.vbias, transpose_b=True)
        pre_sigmoid_hid_p = tf.matmul(vis_samples, self.weights) + self.hbias
        pre_log_term = 1 + tf.exp(pre_sigmoid_hid_p)
        log_term = tf.log(pre_log_term)
        sum_log = tf.reduce_sum(log_term, reduction_indices=1, keep_dims=True)
        assert  (-vbias_term - sum_log).get_shape().as_list() \
            == (vis_samples.get_shape().as_list()[:1] + [1])
        return -vbias_term - sum_log

    def vhv(self, vis_samples):
        hid_samples = self.sample(self.compute_up(vis_samples))
        vis_p = self.compute_down(hid_samples)
        vis_samples = self.sample(vis_p)
        return vis_p, vis_samples
        
    def cd(self, vis, k):
        """Contrastive Divergence.

        params: vis is treated as vis_samples.
        """
        def cond(x, vis_p, vis_samples):
            return tf.less(x, k)

        def body(x, vis_p, vis_samples):
            vis_p, vis_samples = self.vhv(vis_samples)
            return x+1, vis_p, vis_samples

        _, vis_p, vis_samples = tf.while_loop(cond, body, [0, vis, vis],
                                              back_prop=False)
        return vis_p, vis_samples

    def get_loss_updates(self, lr, vis, persistent_vis, cd_k):
        if persistent_vis is not None:
            recon_vis_p, recon_vis_samples = self.cd(persistent_vis, cd_k)
        else:
            recon_vis_p, recon_vis_samples = self.cd(vis, cd_k)

        # treat recon_vis_samples as constant during gradient comp
        recon_vis_samples = tf.stop_gradient(recon_vis_samples)

        # use two reduce mean because vis and pst_chain could have different batch_size
        cost = (tf.reduce_mean(self.free_energy(vis))
                - tf.reduce_mean(self.free_energy(recon_vis_samples)))

        grads = tf.gradients(cost, self.params)
        updates = []
        for grad, param in zip(grads, self.params):
            updates.append(param.assign(param - lr * grad))

        if persistent_vis is not None:
            updates.append(persistent_vis.assign(recon_vis_samples))

        loss = self.l2_loss_function(vis)
        return loss, updates, grads

    def l2_loss_function(self, vis):
        recon_vis_p, _ = self.vhv(vis)
        num_dims = len(vis.get_shape().as_list())
        dims = range(num_dims)
        total_loss = tf.reduce_sum(tf.square(vis - recon_vis_p), reduction_indices=1)#dims[1:])
        return tf.reduce_mean(total_loss)

    def train(self, train_xs, lr, num_epoch, batch_size, use_pcd, cd_k, output_dir):
        vis_shape = train_xs.shape[1:]    # shape of single image
        batch_shape = (batch_size,) + vis_shape
        num_batches = len(train_xs) / batch_size
        assert num_batches * batch_size == len(train_xs)

        # graph related definitions
        ph_vis = tf.placeholder(tf.float32, batch_shape, name='vis_input')
        ph_lr = tf.placeholder(tf.float32, (), name='lr')
        if use_pcd:
            persistent_vis = tf.get_variable(
                'persistent_vis', shape=batch_shape,
                initializer=tf.random_uniform_initializer(0, 1))
        else:
            persistent_vis = None
            
        loss, updates, grads = self.get_loss_updates(ph_lr, ph_vis, persistent_vis, cd_k)

        with self.sess.as_default():
            # merged = tf.merge_all_summaries()
            train_writer = tf.train.SummaryWriter('./train', self.sess.graph)
            tf.initialize_all_variables().run()

            for i in range(num_epoch):
                t = time.time()
                # np.random.shuffle(train_xs)
                loss_vals = np.zeros(num_batches)
                for b in range(num_batches):
                    batch_xs = train_xs[b * batch_size:(b+1) * batch_size]

                    loss_vals[b], _, grad_val = self.sess.run(
                        [loss, updates, grads], feed_dict={ ph_vis: batch_xs, ph_lr: lr })
                    # print (grad_val[0] > 0).sum()
                    # train_writer.add_summary(summary, i)
                print 'Train Loss:', loss_vals.mean()
                print 'Time took:', time.time() - t
                if output_dir is not None:
                    saver = tf.train.Saver()
                    save_path = saver.save(
                        self.sess,
                        os.path.join(output_dir, '%s-epoch%d.ckpt' % (self.name, i)))
                    print 'Model saved to:', save_path
                    prob_imgs, _ = self.sample_from_rbm(100, 1000)
                    img_path = os.path.join(output_dir, 'epoch%d-plot.png' % i)
                    vis_weights(prob_imgs.T, 10, 10, (28, 28), img_path)
                    params = self.get_model_parameters()
                    params_vis_path = os.path.join(output_dir, 'epoch%d-filters.png' % i)
                    vis_weights(params['weights'][:,:100], 10, 10, (28, 28), params_vis_path)

    def load_model(self, model_path):
        with self.sess.as_default():
            tf.initialize_all_variables().run()
            saver = tf.train.Saver()
            saver.restore(self.sess, model_path)
        print 'Model loaded from:', model_path

    def get_model_parameters(self):
        with self.sess.as_default():
            return {
                'weights': self.weights.eval(),
                'vbias': self.vbias.eval(),
                'hbias': self.hbias.eval()
            }

    def sample_from_rbm(self, num_examples, num_steps, init=None):
        num_steps_holder = tf.placeholder(tf.int32, ())
        vis = tf.placeholder(tf.float32, (num_examples, self.num_vis))

        def cond(x, vis_p, vis_samples):
            return tf.less(x, num_steps_holder)

        def body(x, vis_p, vis_samples):
            vis_p, vis_samples = self.vhv(vis_samples)
            return x+1, vis_p, vis_samples

        if init is None:
            init = np.random.normal(0, 1, (num_examples, self.num_vis))

        with self.sess.as_default():
            _, prob_imgs, sampled_imgs = self.sess.run(
                tf.while_loop(cond, body, [0, vis, vis], back_prop=False),
                feed_dict={num_steps_holder: num_steps, vis: init})
        return prob_imgs, sampled_imgs


def vis_weights(weights, rows, cols, neuron_shape, output_name=None):
    assert weights.shape[-1] == rows * cols
    f, axarr = plt.subplots(rows, cols)
    for r in range(rows):
        for c in range(cols):
            neuron_idx = r * cols + c
            weight_map = weights[:, neuron_idx].reshape(neuron_shape)
            axarr[r][c].imshow(weight_map, cmap='Greys')
            axarr[r][c].set_axis_off()
    f.subplots_adjust(hspace=0.2, wspace=0.2)
    if output_name is None:
        plt.show()
    else:
        plt.savefig(output_name)

if __name__ == '__main__':
    (train_xs, _), _, _ = cPickle.load(file('mnist.pkl', 'rb'))

    batch_size =  20
    rbm = RBM(784, 500, 'test')
    # rbm.load_model('./rbm.ckpt')
    # rbm.train(train_xs, 0.001, 5, batch_size, True, None, '.')
    # train(self, train_xs, lr, num_epoch, batch_size, use_pcd, cd_k, output_dir):
    # rbm.train(train_xs, 0.1, 40, batch_size, False, 1, None)
    rbm.train(train_xs, 0.001, 40, batch_size, True, 1, 'test')
