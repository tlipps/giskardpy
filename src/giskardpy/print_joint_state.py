from giskardpy.plugin import OutputPlugin


class PrintJointState(OutputPlugin):
    def __init__(self):
        self.js_identifier = 'js/position/0'

    def start(self):
        pass

    def stop(self):
        pass

    def update(self, databus):
        js = databus.get_data(self.js_identifier)
        print('{}: {}'.format(self.js_identifier, js))