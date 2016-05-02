import astnode as ast
import visitor
import astprint
from calvin.actorstore.store import ActorStore, GlobalStore


def _create_signature(actor_class, actor_type):
    # Create the actor signature to be able to look it up in the GlobalStore if neccessary
    signature_desc = {'is_primitive': True,
                      'actor_type': actor_type,
                      'inports': actor_class.inport_names,
                      'outports': actor_class.outport_names}
    return GlobalStore.actor_signature(signature_desc)



class Finder(object):
    """
    Perform queries on the tree
    FIXME: Make subclass of Visitor
    """
    def __init__(self):
        pass

    @visitor.on('node')
    def visit(self, node):
        pass

    @visitor.when(ast.Node)
    def visit(self, node):
        if node.matches(self.kind, self.attributes):
            self.matches.append(node)
        if not node.is_leaf() and self.depth < self.maxdepth:
            self.depth += 1
            map(self.visit, node.children)
            self.depth -= 1

    def find_all(self, root, kind=None, attributes=None, maxdepth=1024):
        """
        Return a list of all nodes matching <kind>, at most <maxdepth> levels
        down from the starting node <node>
        """
        self.depth = 0
        self.kind = kind
        self.maxdepth = maxdepth
        self.matches = []
        self.attributes = attributes
        self.visit(root)
        return self.matches


class ImplicitPortRewrite(object):
    """
    ImplicitPortRewrite takes care of the construct
        <value> > foo.in
    by replacing <value> with a std.Constant(data=<value>) actor.
    """
    def __init__(self):
        super(ImplicitPortRewrite, self).__init__()
        self.counter = 0

    @visitor.on('node')
    def visit(self, node):
        pass

    @visitor.when(ast.Node)
    def visit(self, node):
        if node.is_leaf():
            return
        map(self.visit, node.children[:])

    @visitor.when(ast.ImplicitPort)
    def visit(self, node):
        const_value = node.children[0]
        args = [ ast.NamedArg(ast.Id('data'), const_value),  ast.NamedArg(ast.Id('n'), ast.Value(-1))]
        self.counter += 1
        const_name = '_literal_const_'+str(self.counter)
        const_actor = ast.Assignment(const_name, 'std.Constant', args)
        const_actor_port = ast.Port(const_name, 'token')
        link = node.parent
        link.replace_child(node, const_actor_port)
        block = link.parent
        block.add_child(const_actor)


class Expander(object):
    """
    Expands a tree with components provided as a dictionary
    """
    def __init__(self, components):
        self.components = components

    @visitor.on('node')
    def visit(self, node):
        pass

    @visitor.when(ast.Node)
    def visit(self, node):
        if node.is_leaf():
            return
        map(self.visit, node.children[:])


    @visitor.when(ast.Assignment)
    def visit(self, node):
        if node.actor_type not in self.components:
            return
        # Clone assignment to clone the arguments
        ca = node.clone()
        args = ca.children
        # Clone block from component definition
        # FIXME: should block be a propery?
        block = self.components[node.actor_type].children[0]
        new = block.clone()
        new.namespace = node.ident
        # Add arguments from assignment to block
        new.args = {x.children[0].ident: x.children[1] for x in args}
        node.parent.replace_child(node, new)
        # Recurse
        # map(self.visit, new.children)
        self.visit(new)


class Flatten(object):
    """
    Flattens a block by wrapping everything in the block's namespace
    and propagating arguments before removing the block
    """
    def __init__(self):
        self.stack = []
        self.constants = {}

    @visitor.on('node')
    def visit(self, node):
        pass

    @visitor.when(ast.Constant)
    def visit(self, node):
        key = node.children[0].ident
        value_node = node.children[1]
        self.constants[key] = value_node

    @visitor.when(ast.Node)
    def visit(self, node):
        if not node.is_leaf():
            map(self.visit, node.children[:])

    @visitor.when(ast.Assignment)
    def visit(self, node):
        self.stack.append(node.ident)
        node.ident = ':'.join(self.stack)
        self.stack.pop()
        map(self.visit, node.children[:])


    @visitor.when(ast.NamedArg)
    def visit(self, node):
        value_node = node.children[1]
        if type(value_node) is ast.Id:
            # Get value from grandparent (block)
            block = node.parent.parent
            key = value_node.ident
            if key not in block.args:
                # Check constants
                if key not in self.constants:
                    print "WARNING: Missing symbol '{}'".format(key)
                    return
                value = self.constants[key]
            else:
                value = block.args[key]
            node.replace_child(value_node, value)


    @visitor.when(ast.Port)
    def visit(self, node):
        if node.actor:
            node.actor = ':'.join(self.stack + [node.actor])
        else:
            node.actor = ':'.join(self.stack)

    @visitor.when(ast.Block)
    def visit(self, node):
        for key, value_node in node.args.iteritems():
            if type(value_node) is ast.Id:
                # Get value from parent (block)
                block = node.parent
                parent_key = value_node.ident
                if parent_key not in block.args:
                    print "WARNING: Missing symbol '{}'".format(parent_key)
                else:
                    value = block.args[parent_key]
                    node.args[key] = value

        if node.namespace:
            self.stack.append(node.namespace)
        # Iterate over a copy of children since we manipulate the list
        map(self.visit, node.children[:])
        if node.namespace:
            self.stack.pop()

        node.parent.add_children(node.children)
        node.delete()


class AppInfo(object):
    """docstring for AppInfo"""
    def __init__(self, script_name):
        super(AppInfo, self).__init__()
        self.actorstore = ActorStore()
        self.app_info = {
            'name':script_name,
            'actors': {},
            'connections': {},
            'valid': True
        }

    @visitor.on('node')
    def visit(self, node):
        pass

    @visitor.when(ast.Node)
    def visit(self, node):
        if not node.is_leaf():
            map(self.visit, node.children)

    @visitor.when(ast.Assignment)
    def visit(self, node):
        namespace = self.app_info['name']
        key = "{}:{}".format(namespace, node.ident)
        value = {}
        found, is_actor, actor_class = self.actorstore.lookup(node.actor_type)
        value['actor_type'] = node.actor_type
        args = {}
        for arg_node in node.children:
            if type(arg_node) is ast.NamedArg:
                arg_id, arg_val = arg_node.children
                args[arg_id.ident] = arg_val.value
        value['args'] = args
        value['signature'] = _create_signature(actor_class, node.actor_type)
        self.app_info['actors'][key] = value

    @visitor.when(ast.Link)
    def visit(self, node):
        namespace = self.app_info['name']
        key = "{}:{}.{}".format(namespace, node.outport.actor, node.outport.port)
        value = "{}:{}.{}".format(namespace, node.inport.actor, node.inport.port)
        self.app_info['connections'].setdefault(key, []).append(value)


class CodeGen(object):
    """
    Generate code from a source file
    FIXME: Use a writer class to generate output in various formats
    """
    def __init__(self, ast_root, script_name):
        super(CodeGen, self).__init__()
        self.actorstore = ActorStore()
        self.root = ast_root
        self.script_name = script_name
        self.constants = {}
        self.local_components = {}
        # self.app_info = {'name':script_name}
        self.printer = astprint.BracePrinter()

        self.run()


    def run(self, verbose=True):
        ast.Node._verbose_desc = verbose

        ##
        # Check for errors
        #

        ##
        # Tree re-write
        #
        # print
        # print "========\nROOT\n========"
        # self.printer.process(self.root)

        ##
        # Expand local components
        #

        components = self.query(self.root, kind=ast.Component, maxdepth=1)
        for c in components:
            self.local_components[c.name] = c

        expander = Expander(self.local_components)
        expander.visit(self.root)
        # All component definitions can now be removed
        for comp in components:
            comp.delete()

        # print "========\nEXPANDED\n========"
        # self.printer.process(self.root)

        ##
        # Implicit port rewrite
        rw = ImplicitPortRewrite()
        rw.visit(self.root)

        # print "========\nPortRewrite\n========"
        # self.printer.process(self.root)

        ##
        # Flatten blocks
        flattener = Flatten()
        flattener.visit(self.root)

        # print "========\nFLATTENED\n========"
        # self.printer.process(self.root)

        ##
        # # Resolve portmaps
        iops = self.query(self.root, kind=ast.InternalOutPort)
        for iop in iops:
            ps = self.query(self.root, kind=ast.InPort, attributes={'actor':iop.actor, 'port':iop.port})
            for p in ps:
                p.parent.inport = iop.parent.inport.clone()

        iips = self.query(self.root, kind=ast.InternalInPort)
        for iip in iips:
            ps = self.query(self.root, kind=ast.OutPort, attributes={'actor':iip.actor, 'port':iip.port})
            for p in ps:
                p.parent.outport = iip.parent.outport.clone()

        for ip in self.query(self.root, kind=ast.InternalOutPort) + self.query(self.root, kind=ast.InternalInPort):
            ip.parent.delete()

        # print "========\nFINISHED\n========"
        # self.printer.process(self.root)

        ##
        # "code" generation
        gen_app_info = AppInfo(self.script_name)
        gen_app_info.visit(self.root)
        self.app_info = gen_app_info.app_info

        import json
        print json.dumps(self.app_info, indent=4)

    def query(self, root, kind=None, attributes=None, maxdepth=1024):
        finder = Finder()
        finder.find_all(root, kind, attributes=attributes, maxdepth=maxdepth)
        return finder.matches


if __name__ == '__main__':
    from parser_regression_tests import run_check
    run_check(tests=['test9'], print_diff=True, print_script=True, testdir='/Users/eperspe/Source/calvin-base/calvin/examples/sample-scripts')
    # run_check(tests=['test11'], print_diff=True, print_script=True, testdir='/Users/eperspe/Source/calvin-base/calvin/tests/scripts')

