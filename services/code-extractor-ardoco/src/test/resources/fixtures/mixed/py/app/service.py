class Service:
    def run(self, name):
        return helper(name)


def helper(name):
    return name.upper()
