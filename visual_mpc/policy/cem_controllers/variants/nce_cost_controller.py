from visual_mpc.policy.cem_controllers import CEMBaseController
import imp
import control_embedding
import numpy as np
from visual_mpc.video_prediction.pred_util import get_context, rollout_predictions


class NCECostController(CEMBaseController):
    """
    Cross Entropy Method Stochastic Optimizer
    """
    def __init__(self, ag_params, policyparams, gpu_id, ngpu):
        """

        :param ag_params: agent parameters
        :param policyparams: policy parameters
        :param gpu_id: starting gpu id
        :param ngpu: number of gpus
        """
        CEMBaseController.__init__(self, ag_params, policyparams)

        params = imp.load_source('params', ag_params['current_dir'] + '/conf.py')
        net_conf = params.configuration

        if ngpu > 1:
            vpred_ngpu = ngpu - 1
        else: vpred_ngpu = ngpu

        self._predictor = net_conf['setup_predictor'](ag_params, net_conf, gpu_id, vpred_ngpu, self._logger)
        self._scoring_func = control_embedding.deploy_model(self._hp.nce_conf_path, batch_size=self._hp.nce_batch_size,
                                                            restore_path=self._hp.nce_restore_path,
                                                            device_id=gpu_id + ngpu - 1)

        self._vpred_bsize = net_conf['batch_size']

        self._seqlen = net_conf['sequence_length']
        self._net_context = net_conf['context_frames']
        self._n_pred = self._seqlen - self._net_context
        assert self._n_pred > 0, "context_frames must be larger than sequence_length"

        self._img_height, self._img_width = net_conf['orig_size']

        self._n_cam = net_conf['ncam']

        self._images = None
        self._goal_image = None
        self._start_image = None

    def _default_hparams(self):
        default_dict = {
            'finalweight': 10,
            'nce_conf_path': '',
            'nce_restore_path': '',
            'nce_batch_size': 200,
            'state_append': None
        }
        parent_params = super(NCECostController, self)._default_hparams()

        for k in default_dict.keys():
            parent_params.add_hparam(k, default_dict[k])
        return parent_params

    def evaluate_rollouts(self, actions, cem_itr):
        last_frames, last_states = get_context(self._net_context, self._t,
                                               self._state, self._images, self._hp)

        gen_images = rollout_predictions(self.predictor, self._net_bsize, actions,
                                         last_frames, last_states, logger=self._logger)[0]

        gen_images = np.concatenate(gen_images, 0) * 255.

        scores = np.zeros((self._n_cam, actions.shape[0], self._n_pred))
        for c in range(self._n_cam):
            goal, start = self.goal_image[c][None], self._start_image[c][None]
            input_images = gen_images[:, :, c].reshape((-1, self._img_height, self._img_width, 3))
            embed_dict = self._scoring_func(goal, start, input_images)

            gs_enc = embed_dict['goal_enc'][0][None]
            in_enc = embed_dict['input_enc'].reshape((actions.shape[0], self._n_pred, -1))
            scores[c] = -np.matmul(gs_enc[None], np.swapaxes(in_enc, 2, 1))[:, 0]

        scores = np.sum(scores, axis=0)
        scores[:, -1] *= self._hp.finalweight

        return scores

    def act(self, t=None, i_tr=None, goal_image=None, images=None, state=None):
        """
        Return a random action for a state.
        Args:
            if performing highres tracking images is highres image
            t: the current controller's Time step
            goal_pix: in coordinates of small image
            desig_pix: in coordinates of small image
        """
        self._start_image = images[-1].astype(np.float32)
        self._goal_image = goal_image[1] * 255
        self._images = images

        return super(NCECostController, self).act(t, i_tr)
